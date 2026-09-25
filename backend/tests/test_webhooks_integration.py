"""Webhook payload fixtures -> DB state, through the real HTTP endpoint."""

import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.api.deps import get_github, get_publisher
from nextix.db.models import Repo, Ticket, WebhookDelivery
from nextix.db.session import get_db
from nextix.github.client import GitHubClient
from nextix.main import create_app
from tests.conftest import FakePublisher, load_fixture

SECRET = "test-webhook-secret"


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_github] = lambda: gh
    app.dependency_overrides[get_publisher] = lambda: publisher
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def deliver(
    client: httpx.AsyncClient,
    event: str,
    payload: dict[str, Any],
    *,
    delivery: str | None = None,
    secret: str = SECRET,
) -> httpx.Response:
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return await client.post(
        "/api/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )


async def ticket(session: AsyncSession, number: int = 42) -> Ticket | None:
    return await session.scalar(
        select(Ticket)
        .where(Ticket.issue_number == number)
        .execution_options(populate_existing=True)
    )


# ---------------------------------------------------------------- signature / dedupe


async def test_rejects_bad_signature(client: httpx.AsyncClient, session: AsyncSession) -> None:
    r = await deliver(client, "issues", load_fixture("issues_labeled.json"), secret="wrong")
    assert r.status_code == 401
    assert await ticket(session) is None


async def test_rejects_unsigned(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/github/webhook",
        content=b"{}",
        headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "x"},
    )
    assert r.status_code == 401


async def test_duplicate_delivery_is_processed_once(
    client: httpx.AsyncClient, session: AsyncSession, publisher: FakePublisher
) -> None:
    payload = load_fixture("issues_labeled.json")
    first = await deliver(client, "issues", payload, delivery="d-1")
    second = await deliver(client, "issues", payload, delivery="d-1")
    assert first.json()["status"] == "ok"
    assert second.json()["status"] == "duplicate"
    assert len(publisher.events) == 1
    assert len((await session.scalars(select(WebhookDelivery))).all()) == 1


async def test_unknown_event_is_acknowledged(client: httpx.AsyncClient) -> None:
    r = await deliver(client, "star", {"action": "created"})
    assert r.status_code == 200
    assert "ignored" in r.json()["note"]


# ---------------------------------------------------------------- issues


async def test_labeling_creates_ticket_in_todo(
    client: httpx.AsyncClient, session: AsyncSession, publisher: FakePublisher
) -> None:
    r = await deliver(client, "issues", load_fixture("issues_labeled.json"))
    assert r.status_code == 200, r.text

    t = await ticket(session)
    assert t is not None
    assert t.title == "Add a dark mode toggle to settings"
    assert t.issue_state == "open"
    assert set(t.labels) == {"nextix", "ui"}
    assert t.created_via == "github"
    repo = await session.scalar(select(Repo))
    assert repo is not None
    assert (repo.owner, repo.name, repo.installation_id, repo.default_branch) == (
        "acme",
        "widgets",
        777,
        "main",
    )

    [(channel, event, data)] = publisher.events
    assert (channel, event) == ("nextix:board", "ticket.updated")
    assert data["column"] == "todo"
    assert data["repo"] == "acme/widgets"
    assert data["issue_url"] == "https://github.com/acme/widgets/issues/42"


async def test_unlabeled_issue_is_ignored(client: httpx.AsyncClient, session: AsyncSession) -> None:
    r = await deliver(client, "issues", load_fixture("issues_opened_unlabeled.json"))
    assert r.json()["note"] == "not a nextix issue"
    assert await ticket(session, 50) is None


async def test_removing_label_takes_ticket_off_board(
    client: httpx.AsyncClient, session: AsyncSession, publisher: FakePublisher
) -> None:
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    await deliver(client, "issues", load_fixture("issues_unlabeled.json"))
    t = await ticket(session)
    assert t is not None and t.labels == ["ui"]
    assert publisher.events[-1][1] == "ticket.removed"


async def test_closing_issue_moves_to_done(
    client: httpx.AsyncClient, session: AsyncSession, publisher: FakePublisher
) -> None:
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    await deliver(client, "issues", load_fixture("issues_closed.json"))
    t = await ticket(session)
    assert t is not None and t.issue_state == "closed"
    assert publisher.events[-1][2]["column"] == "done"


async def test_deleted_issue_leaves_board(
    client: httpx.AsyncClient, session: AsyncSession, publisher: FakePublisher
) -> None:
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    await deliver(client, "issues", load_fixture("issues_deleted.json"))
    t = await ticket(session)
    assert t is not None and t.issue_state == "deleted"
    assert publisher.events[-1][1] == "ticket.removed"


async def test_disabled_repo_is_ignored(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    repo = await session.scalar(select(Repo))
    assert repo is not None
    repo.enabled = False
    await session.commit()

    payload = load_fixture("issues_labeled.json")
    payload["issue"]["title"] = "renamed"
    r = await deliver(client, "issues", payload)
    assert r.json()["note"] == "repo disabled or unknown"
    t = await ticket(session)
    assert t is not None and t.title != "renamed"


# ---------------------------------------------------------------- pull requests


async def test_pr_lifecycle_in_review_then_done(
    client: httpx.AsyncClient, session: AsyncSession, publisher: FakePublisher
) -> None:
    await deliver(client, "issues", load_fixture("issues_labeled.json"))

    await deliver(client, "pull_request", load_fixture("pull_request_opened.json"))
    t = await ticket(session)
    assert t is not None and (t.pr_number, t.pr_state) == (43, "open")
    assert publisher.events[-1][2]["column"] == "in_review"
    assert publisher.events[-1][2]["pr_url"] == "https://github.com/acme/widgets/pull/43"

    await deliver(client, "pull_request", load_fixture("pull_request_closed_merged.json"))
    t = await ticket(session)
    assert t is not None and t.pr_state == "merged"
    assert publisher.events[-1][2]["column"] == "done"


async def test_pr_from_other_branch_is_ignored(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    payload = load_fixture("pull_request_opened.json")
    payload["pull_request"]["head"]["ref"] = "feature/issue-42"
    r = await deliver(client, "pull_request", payload)
    assert r.json()["note"] == "not a nextix branch"
    t = await ticket(session)
    assert t is not None and t.pr_number is None


async def test_pr_from_fork_is_ignored(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    payload = load_fixture("pull_request_opened.json")
    payload["pull_request"]["head"]["repo"] = {
        "id": 1,
        "name": "widgets",
        "full_name": "mallory/widgets",
    }
    await deliver(client, "pull_request", payload)
    t = await ticket(session)
    assert t is not None and t.pr_number is None


async def test_stale_closed_pr_does_not_replace_open_pr(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    await deliver(client, "pull_request", load_fixture("pull_request_opened.json"))
    old = load_fixture("pull_request_closed_merged.json")
    old["pull_request"].update(number=40, merged=False, merged_at=None)
    await deliver(client, "pull_request", old)
    t = await ticket(session)
    assert t is not None and (t.pr_number, t.pr_state) == (43, "open")


# ---------------------------------------------------------------- installations


def _gh_repo(name: str) -> dict[str, Any]:
    base = load_fixture("issues_labeled.json")["repository"]
    return {**base, "id": sum(map(ord, name)), "name": name, "full_name": f"acme/{name}"}


def mock_installation_repos(respx_mock: respx.MockRouter, *names: str) -> None:
    """What GitHub says installation 777 can access right now."""
    respx_mock.get("/installation/repositories").respond(
        200,
        json={"total_count": len(names), "repositories": [_gh_repo(n) for n in names]},
    )


async def repos_by_name(session: AsyncSession) -> dict[str, Repo]:
    rows = await session.scalars(select(Repo).execution_options(populate_existing=True))
    return {r.name: r for r in rows}


async def test_installation_created_adds_repos(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    mock_installation_repos(respx_mock, "widgets", "gadgets")
    r = await deliver(client, "installation", load_fixture("installation_created.json"))
    assert r.status_code == 200, r.text
    repos = await repos_by_name(session)
    assert set(repos) == {"widgets", "gadgets"}
    widgets = repos["widgets"]
    assert (
        widgets.full_name,
        widgets.installation_id,
        widgets.default_branch,
        widgets.enabled,
    ) == (
        "acme/widgets",
        777,
        "main",
        True,
    )


async def test_switch_from_all_to_selected_retires_dropped_repos(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    """Regression: GitHub's event for this switch lists only the added repo.

    Installed on "all" (widgets, gadgets, gizmos), then narrowed to just "board".
    The payload says added=[board], removed=[] -- the three dropped repos only
    disappear because we reconcile against GitHub's full list.
    """
    mock_installation_repos(respx_mock, "widgets", "gadgets", "gizmos")
    await deliver(client, "installation", load_fixture("installation_created.json"))
    await deliver(client, "issues", load_fixture("issues_labeled.json"))  # widgets gets a ticket

    mock_installation_repos(respx_mock, "board")
    payload = load_fixture("installation_repositories_removed.json")
    payload.update(
        action="added",
        repositories_added=[{"id": 1, "name": "board", "full_name": "acme/board", "private": True}],
        repositories_removed=[],
    )
    r = await deliver(client, "installation_repositories", payload)
    assert r.json()["note"] == "1 repos active, 1 disabled, 2 removed"

    repos = await repos_by_name(session)
    assert set(repos) == {"board", "widgets"}  # ticket-less gadgets/gizmos deleted
    assert repos["board"].enabled is True
    assert repos["widgets"].enabled is False  # has a ticket: kept, disabled
    assert await ticket(session) is not None


async def test_repo_removed_from_installation_is_disabled(
    client: httpx.AsyncClient,
    session: AsyncSession,
    publisher: FakePublisher,
    respx_mock: respx.MockRouter,
) -> None:
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    mock_installation_repos(respx_mock)  # nothing accessible any more
    await deliver(
        client, "installation_repositories", load_fixture("installation_repositories_removed.json")
    )
    repo = await session.scalar(select(Repo).execution_options(populate_existing=True))
    assert repo is not None and repo.enabled is False
    assert await ticket(session) is not None  # history kept
    assert publisher.events[-1][1] == "ticket.removed"


async def test_readding_a_repo_enables_it_again(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    mock_installation_repos(respx_mock)
    await deliver(
        client, "installation_repositories", load_fixture("installation_repositories_removed.json")
    )
    mock_installation_repos(respx_mock, "widgets")
    payload = load_fixture("installation_repositories_removed.json")
    payload.update(
        action="added",
        repositories_added=payload["repositories_removed"],
        repositories_removed=[],
    )
    await deliver(client, "installation_repositories", payload)
    assert (await repos_by_name(session))["widgets"].enabled is True


async def test_installation_deleted_retires_all_repos(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    mock_installation_repos(respx_mock, "widgets", "gadgets")
    await deliver(client, "installation", load_fixture("installation_created.json"))
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    payload = load_fixture("installation_created.json")
    payload["action"] = "deleted"
    await deliver(client, "installation", payload)
    repos = await repos_by_name(session)
    assert set(repos) == {"widgets"}  # gadgets had no tickets
    assert repos["widgets"].enabled is False
