"""Regressions: repo retirement with CI rows, installation re-enabling, case-insensitive
repo names, and webhook triggers that survive a queue outage."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.api.deps import get_enqueuer, get_github, get_publisher
from nextix.db.models import CheckRun, CommitStatus, Repo, Run, Ticket
from nextix.db.session import get_db
from nextix.github.client import GitHubClient
from nextix.main import create_app
from nextix.tickets import service
from tests.conftest import FakeEnqueuer, FakePublisher, load_fixture
from tests.test_webhooks_integration import (
    deliver,
    labeled_by,
    mock_installation_repos,
    repos_by_name,
)

REPO = "/repos/acme/widgets"


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    enqueuer: FakeEnqueuer,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_github] = lambda: gh
    app.dependency_overrides[get_publisher] = lambda: publisher
    app.dependency_overrides[get_enqueuer] = lambda: enqueuer
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def installation(action: str) -> dict[str, Any]:
    payload = load_fixture("installation_created.json")
    payload["action"] = action
    return payload


# ------------------------------------------------------------------ 1. retiring repos


async def add_ci_rows(session: AsyncSession, repo: Repo) -> None:
    session.add(CheckRun(id=1, repo_id=repo.id, head_sha="abc", name="ci", status="completed"))
    session.add(CommitStatus(repo_id=repo.id, sha="abc", context="ci/legacy", state="success"))
    await session.commit()


async def test_a_ticketless_repo_with_ci_rows_is_retired_cleanly(session: AsyncSession) -> None:
    repo = Repo(owner="acme", name="gadgets", installation_id=777, default_branch="main")
    session.add(repo)
    await session.flush()
    await add_ci_rows(session, repo)

    result = await service.retire_repos(session, 777)
    await session.commit()

    assert (result.deleted, result.disabled) == (1, 0)
    assert await session.scalar(select(func.count()).select_from(Repo)) == 0
    assert await session.scalar(select(func.count()).select_from(CheckRun)) == 0
    assert await session.scalar(select(func.count()).select_from(CommitStatus)) == 0


async def test_uninstalling_with_ci_rows_does_not_fail_the_webhook(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    mock_installation_repos(respx_mock, "widgets", "gadgets")
    await deliver(client, "installation", installation("created"))
    await add_ci_rows(session, (await repos_by_name(session))["gadgets"])

    r = await deliver(client, "installation", installation("deleted"))
    assert r.status_code == 200, r.text
    assert set(await repos_by_name(session)) == set()


# ------------------------------------------------------------------ 2. re-enabling


async def test_unsuspending_brings_the_repos_back(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    mock_installation_repos(respx_mock, "widgets", "gadgets")
    await deliver(client, "installation", installation("created"))
    await deliver(client, "issues", load_fixture("issues_labeled.json"))

    await deliver(client, "installation", installation("suspend"))
    assert {r.enabled for r in (await repos_by_name(session)).values()} == {False}

    r = await deliver(client, "installation", installation("unsuspend"))
    assert r.status_code == 200, r.text
    repos = await repos_by_name(session)
    assert set(repos) == {"widgets", "gadgets"}
    assert {r.enabled for r in repos.values()} == {True}


async def test_reinstalling_brings_the_kept_repos_back(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    mock_installation_repos(respx_mock, "widgets", "gadgets")
    await deliver(client, "installation", installation("created"))
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    await deliver(client, "installation", installation("deleted"))
    assert (await repos_by_name(session))["widgets"].enabled is False  # kept for its ticket

    await deliver(client, "installation", installation("created"))
    repos = await repos_by_name(session)
    assert repos["widgets"].enabled is True and repos["gadgets"].enabled is True
    board = await service.list_board(session)
    assert [c.issue_number for c in board] == [42]


# ------------------------------------------------------------------ 7. case


async def test_repo_names_match_case_insensitively(session: AsyncSession) -> None:
    session.add(Repo(owner="Acme", name="Widgets", installation_id=777, default_branch="main"))
    await session.commit()

    found = await service.get_repo(session, "acme", "WIDGETS")
    assert found is not None and found.full_name == "Acme/Widgets"

    await service.set_repos_enabled(
        session, enabled=False, installation_id=777, full_names=["ACME/widgets"]
    )
    await session.commit()
    await session.refresh(found)
    assert found.enabled is False


# ------------------------------------------------------------------ 4. queue outages


@pytest.fixture
def issue_comments(respx_mock: respx.MockRouter) -> respx.Route:
    return respx_mock.post(f"{REPO}/issues/42/comments").respond(201, json={})


async def tracked_ticket(client: httpx.AsyncClient, session: AsyncSession) -> Ticket:
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    await deliver(client, "pull_request", load_fixture("pull_request_opened.json"))
    found = await session.scalar(select(Ticket).where(Ticket.issue_number == 42))
    assert found is not None and found.pr_number == 43
    return found


def review_payload() -> dict[str, Any]:
    payload = load_fixture("pull_request_opened.json")
    payload["action"] = "submitted"
    payload["review"] = {
        "id": 9001,
        "user": {"login": "acme"},
        "state": "changes_requested",
        "body": "Use tabs.",
        "html_url": "https://github.com/acme/widgets/pull/43#pullrequestreview-9001",
    }
    payload["sender"] = {"login": "acme"}
    return payload


def pr_comment_payload() -> dict[str, Any]:
    payload = load_fixture("issues_labeled.json")
    payload["action"] = "created"
    payload["issue"]["number"] = 43
    payload["issue"]["pull_request"] = {"url": "x"}
    payload["issue"]["labels"] = []
    payload["comment"] = {
        "id": 6,
        "user": {"login": "acme"},
        "body": "Make it green instead.",
        "html_url": "https://github.com/acme/widgets/pull/43#issuecomment-6",
    }
    payload["sender"] = {"login": "acme"}
    return payload


async def test_a_review_survives_a_queue_outage(
    client: httpx.AsyncClient,
    session: AsyncSession,
    enqueuer: FakeEnqueuer,
    issue_comments: respx.Route,
    respx_mock: respx.MockRouter,
) -> None:
    ticket = await tracked_ticket(client, session)
    respx_mock.get(f"{REPO}/pulls/43/reviews/9001/comments").respond(200, json=[])
    enqueuer.fail = True
    r = await deliver(client, "pull_request_review", review_payload())
    assert r.status_code == 200, r.text
    await session.refresh(ticket)
    # Parked for the reaper's start_pending_reviews, like a review that arrives mid-run.
    assert ticket.pending_review_id == 9001


async def test_a_pr_comment_survives_a_queue_outage(
    client: httpx.AsyncClient,
    session: AsyncSession,
    enqueuer: FakeEnqueuer,
    issue_comments: respx.Route,
) -> None:
    ticket = await tracked_ticket(client, session)
    enqueuer.fail = True
    r = await deliver(client, "issue_comment", pr_comment_payload())
    assert r.status_code == 200, r.text
    await session.refresh(ticket)
    assert ticket.pending_comment and ticket.pending_comment["body"] == "Make it green instead."


async def test_a_label_trigger_in_an_outage_is_cancelled_for_a_retry(
    client: httpx.AsyncClient,
    session: AsyncSession,
    enqueuer: FakeEnqueuer,
    issue_comments: respx.Route,
) -> None:
    enqueuer.fail = True
    r = await deliver(client, "issues", labeled_by("acme"))
    assert r.status_code == 200, r.text
    run = await session.scalar(select(Run).execution_options(populate_existing=True))
    assert run is not None and (run.status, run.exit_reason) == ("cancelled", "enqueue_failed")
    ticket = await session.scalar(select(Ticket))
    assert ticket is not None and ticket.pending_review_id is None and not ticket.pending_comment
    said = [json.loads(c.request.content)["body"] for c in issue_comments.calls]
    assert any("Retry it from the board" in s for s in said)
