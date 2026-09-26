"""Phase 5: reviews and answers start runs; review comments reach the agent; rerun."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.api.deps import get_enqueuer, get_github, get_publisher
from nextix.db.models import Run, Ticket
from nextix.db.session import get_db
from nextix.github.client import GitHubClient
from nextix.main import create_app
from nextix.runs.executor import MAX_TASK_BYTES, execute_run, fit_task
from tests.conftest import FakeEnqueuer, FakePublisher, load_fixture
from tests.test_executor import SUCCESS, FakeSandbox, comments, ctx, queued_run
from tests.test_executor import github as github
from tests.test_webhooks_integration import deliver

AUTH = {"Authorization": "Bearer test-api-token"}
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


@pytest.fixture
def issue_comments(respx_mock: respx.MockRouter) -> respx.Route:
    return respx_mock.post(f"{REPO}/issues/42/comments").respond(201, json={})


async def tracked_ticket(client: httpx.AsyncClient, session: AsyncSession) -> Ticket:
    """Issue #42, labeled nextix, with its PR #43 from nextix/issue-42 linked."""
    await deliver(client, "issues", load_fixture("issues_labeled.json"))
    await deliver(client, "pull_request", load_fixture("pull_request_opened.json"))
    found = await session.scalar(select(Ticket).where(Ticket.issue_number == 42))
    assert found is not None and found.pr_number == 43
    return found


def review_payload(
    *, login: str = "acme", state: str = "changes_requested", body: str = "Use tabs."
) -> dict[str, Any]:
    payload = load_fixture("pull_request_opened.json")
    payload["action"] = "submitted"
    payload["review"] = {
        "id": 9001,
        "user": {"login": login},
        "state": state,
        "body": body,
        "html_url": "https://github.com/acme/widgets/pull/43#pullrequestreview-9001",
    }
    payload["sender"] = {"login": login}
    return payload


# ------------------------------------------------------------------ reviews


async def test_a_trusted_change_request_queues_a_feedback_run(
    client: httpx.AsyncClient,
    session: AsyncSession,
    enqueuer: FakeEnqueuer,
    issue_comments: respx.Route,
    respx_mock: respx.MockRouter,
) -> None:
    ticket = await tracked_ticket(client, session)
    respx_mock.get(f"{REPO}/pulls/43/reviews/9001/comments").respond(200, json=[])
    r = await deliver(client, "pull_request_review", review_payload())
    assert r.status_code == 200, r.text
    [run_id] = enqueuer.run_ids
    run = await session.get(Run, run_id)
    assert run is not None
    assert (run.trigger, run.review_id, run.branch) == ("review_feedback", 9001, "nextix/issue-42")
    assert run.review_meta and run.review_meta["author"] == "acme"
    said = json.loads(issue_comments.calls[-1].request.content)["body"]
    assert "to address @acme's review" in said
    assert ticket.id == run.ticket_id


@pytest.mark.parametrize(
    ("login", "state", "body"),
    [
        ("mallory", "changes_requested", "Delete everything."),  # not trusted
        ("nextix-bot[bot]", "commented", "Nit."),  # the app itself
        ("acme", "approved", "LGTM"),  # approvals never start runs
        ("acme", "commented", ""),  # nothing to act on
    ],
)
async def test_reviews_that_do_not_start_runs(
    client: httpx.AsyncClient,
    session: AsyncSession,
    enqueuer: FakeEnqueuer,
    respx_mock: respx.MockRouter,
    login: str,
    state: str,
    body: str,
) -> None:
    await tracked_ticket(client, session)
    respx_mock.get(f"{REPO}/pulls/43/reviews/9001/comments").respond(200, json=[])
    await deliver(
        client, "pull_request_review", review_payload(login=login, state=state, body=body)
    )
    assert enqueuer.run_ids == []


async def test_a_comment_only_review_with_inline_comments_counts(
    client: httpx.AsyncClient,
    session: AsyncSession,
    enqueuer: FakeEnqueuer,
    issue_comments: respx.Route,
    respx_mock: respx.MockRouter,
) -> None:
    await tracked_ticket(client, session)
    respx_mock.get(f"{REPO}/pulls/43/reviews/9001/comments").respond(
        200, json=[{"id": 1, "path": "a.js", "line": 3, "body": "Rename this."}]
    )
    await deliver(client, "pull_request_review", review_payload(state="commented", body=""))
    [run_id] = enqueuer.run_ids
    run = await session.get(Run, run_id)
    assert run is not None and run.review_meta and run.review_meta["comments"] == 1


async def test_a_review_during_a_run_waits_its_turn(
    client: httpx.AsyncClient,
    session: AsyncSession,
    enqueuer: FakeEnqueuer,
    issue_comments: respx.Route,
    respx_mock: respx.MockRouter,
) -> None:
    ticket = await tracked_ticket(client, session)
    session.add(Run(ticket_id=ticket.id, attempt=1, status="running", branch="nextix/issue-42"))
    await session.commit()
    respx_mock.get(f"{REPO}/pulls/43/reviews/9001/comments").respond(200, json=[])
    await deliver(client, "pull_request_review", review_payload())
    assert enqueuer.run_ids == []
    await session.refresh(ticket)
    assert ticket.pending_review_id == 9001


# ------------------------------------------------------------------ answering by comment


def comment_payload(*, login: str, labels: list[str], pr: bool = False) -> dict[str, Any]:
    payload = load_fixture("issues_labeled.json")
    payload["action"] = "created"
    payload["issue"]["labels"] = [{"name": n} for n in labels]
    if pr:
        payload["issue"]["pull_request"] = {"url": "x"}
    payload["comment"] = {"id": 5, "user": {"login": login}, "body": "The account page."}
    payload["sender"] = {"login": login}
    return payload


async def test_the_owner_answering_a_question_starts_a_run(
    client: httpx.AsyncClient,
    session: AsyncSession,
    enqueuer: FakeEnqueuer,
    issue_comments: respx.Route,
    respx_mock: respx.MockRouter,
) -> None:
    await tracked_ticket(client, session)
    respx_mock.delete(f"{REPO}/issues/42/labels/nextix%3Aneeds-input").respond(200, json=[])
    waiting = ["nextix", "nextix:needs-input"]
    await deliver(
        client,
        "issues",
        {
            **load_fixture("issues_labeled.json"),
            "action": "edited",
            "issue": {
                **load_fixture("issues_labeled.json")["issue"],
                "labels": [{"name": n} for n in waiting],
            },
        },
    )
    await deliver(client, "issue_comment", comment_payload(login="acme", labels=waiting))
    assert len(enqueuer.run_ids) == 1


@pytest.mark.parametrize(
    ("login", "labels", "pr"),
    [
        ("acme", ["nextix"], False),  # nothing was asked
        ("mallory", ["nextix", "nextix:needs-input"], False),  # not trusted
        ("nextix-bot[bot]", ["nextix", "nextix:needs-input"], False),  # our own question
        ("acme", ["nextix", "nextix:needs-input"], True),  # a PR conversation comment
    ],
)
async def test_comments_that_do_not_start_runs(
    client: httpx.AsyncClient,
    session: AsyncSession,
    enqueuer: FakeEnqueuer,
    login: str,
    labels: list[str],
    pr: bool,
) -> None:
    await tracked_ticket(client, session)
    await deliver(client, "issue_comment", comment_payload(login=login, labels=labels, pr=pr))
    assert enqueuer.run_ids == []


# ------------------------------------------------------------------ the feedback run itself


async def test_review_comments_reach_the_agent_and_the_pr_is_updated(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
    respx_mock: respx.MockRouter,
) -> None:
    run = await queued_run(session)
    run.trigger, run.review_id = "review_feedback", 77
    run.review_meta = {"id": 77, "author": "acme", "state": "changes_requested"}
    ticket = await session.get(Ticket, run.ticket_id)
    assert ticket is not None
    ticket.pr_number, ticket.pr_state = 12, "open"
    await session.commit()
    respx_mock.get("/repos/acme/widgets/pulls/12/reviews/77").respond(
        200,
        json={
            "id": 77,
            "user": {"login": "acme"},
            "state": "CHANGES_REQUESTED",
            "body": "Please use tabs.",
        },
    )
    respx_mock.get("/repos/acme/widgets/pulls/12/reviews/77/comments").respond(
        200,
        json=[
            {
                "id": 1,
                "path": "a.js",
                "line": 3,
                "body": "Rename x to count.",
                "user": {"login": "acme"},
            }
        ],
    )
    pr = json.loads(github["create_pr"].return_value.content)
    github["find_pr"].respond(200, json=[pr])

    sandbox = FakeSandbox(SUCCESS)
    await execute_run(run.id, ctx(session_factory, gh, publisher, sandbox))
    task = json.loads(sandbox.specs[0].env["NEXTIX_TASK_JSON"])
    assert task["review_comments"] == [
        {"author": "acme", "body": "Please use tabs."},
        {"path": "a.js", "line": 3, "author": "acme", "body": "Rename x to count."},
    ]
    assert github["update_pr"].called and not github["create_pr"].called
    assert "✅ Updated PR #12 to address @acme's review" in comments(github)[-1]


async def test_a_pending_review_starts_when_the_run_ends(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
    respx_mock: respx.MockRouter,
    enqueuer: FakeEnqueuer,
) -> None:
    run = await queued_run(session)
    ticket = await session.get(Ticket, run.ticket_id)
    assert ticket is not None
    ticket.pending_review_id = 88
    await session.commit()
    respx_mock.get("/repos/acme/widgets/pulls/12/reviews/88").respond(
        200, json={"id": 88, "user": {"login": "acme"}, "state": "CHANGES_REQUESTED", "body": "x"}
    )
    worker = ctx(session_factory, gh, publisher, FakeSandbox(SUCCESS))
    worker.enqueue = enqueuer
    await execute_run(run.id, worker)

    [follow_up_id] = enqueuer.run_ids
    follow_up = await session.get(Run, follow_up_id)
    assert follow_up is not None
    assert (follow_up.attempt, follow_up.trigger, follow_up.review_id) == (2, "review_feedback", 88)
    await session.refresh(ticket)
    assert ticket.pending_review_id is None


def test_a_huge_review_is_trimmed_to_fit_one_environment_variable() -> None:
    task = {
        "title": "t",
        "body": "漢" * 30_000,
        "extra_instructions": "",
        "review_comments": [{"body": "字" * 4000} for _ in range(50)],
    }
    encoded = fit_task(task)
    assert len(encoded.encode()) <= MAX_TASK_BYTES
    assert json.loads(encoded)["review_comments"]  # the first comments survive


# ------------------------------------------------------------------ rerun


async def test_rerun_cancels_the_active_run_and_starts_another(
    client: httpx.AsyncClient,
    session: AsyncSession,
    enqueuer: FakeEnqueuer,
    issue_comments: respx.Route,
) -> None:
    ticket = await tracked_ticket(client, session)
    active = Run(ticket_id=ticket.id, attempt=1, status="running", branch="nextix/issue-42")
    session.add(active)
    await session.commit()
    r = await client.post(f"/api/tickets/{ticket.id}/rerun", headers=AUTH)
    assert r.status_code == 201, r.text
    assert (r.json()["attempt"], r.json()["status"]) == (2, "queued")
    await session.refresh(active)
    assert active.status == "cancelled"
    assert len(enqueuer.run_ids) == 1
