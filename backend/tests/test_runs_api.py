"""Run lifecycle through the HTTP API: the sandbox callback, retry, cancel, and reads."""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.api.deps import get_enqueuer, get_github, get_publisher
from nextix.api.internal import sign
from nextix.db.models import Repo, Run, RunEvent, Ticket
from nextix.db.session import get_db
from nextix.github.client import GitHubClient
from nextix.main import create_app
from nextix.runs.events import TOOL_RESULT_LIMIT
from nextix.runs.lifecycle import ActiveRunExists, enqueue_run
from nextix.runs.state import InvalidTransition, RunStatus, apply_transition, can_transition
from tests.conftest import FakeEnqueuer, FakePublisher

AUTH = {"Authorization": "Bearer test-api-token"}
SECRET = "c" * 64
COMMENTS = "/repos/acme/widgets/issues/7/comments"


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
def comments(respx_mock: respx.MockRouter) -> respx.Route:
    return respx_mock.post(COMMENTS).respond(201, json={})


async def make_ticket(session: AsyncSession, *, state: str = "open") -> Ticket:
    repo = Repo(owner="acme", name="widgets", installation_id=777, default_branch="main")
    session.add(repo)
    await session.flush()
    ticket = Ticket(
        repo_id=repo.id, issue_number=7, title="Add dark mode", issue_state=state, labels=["nextix"]
    )
    session.add(ticket)
    await session.commit()
    return ticket


async def make_run(
    session: AsyncSession, ticket: Ticket, status: str = "claimed", **kw: Any
) -> Run:
    run = Run(
        ticket_id=ticket.id,
        attempt=kw.pop("attempt", 1),
        status=status,
        branch="nextix/issue-7",
        agent_id="agent-abc123",
        callback_secret=SECRET,
        started_at=datetime.now(UTC),
        last_heartbeat=datetime.now(UTC) - timedelta(seconds=60),
        **kw,
    )
    session.add(run)
    await session.commit()
    return run


async def callback(
    client: httpx.AsyncClient, run_id: uuid.UUID, events: list[dict[str, Any]], secret: str = SECRET
) -> httpx.Response:
    body = json.dumps({"events": events}).encode()
    return await client.post(
        f"/api/internal/runs/{run_id}/events",
        content=body,
        headers={"X-Nextix-Signature": sign(secret, body), "Content-Type": "application/json"},
    )


# ------------------------------------------------------------------ state machine


def test_transitions_follow_the_spec() -> None:
    assert can_transition("queued", "claimed")
    assert can_transition("claimed", "running")
    assert can_transition("running", "succeeded")
    assert can_transition("running", "needs_input")
    assert not can_transition("queued", "running")  # must be claimed first
    assert not can_transition("succeeded", "running")  # terminal
    assert not can_transition("failed", "queued")  # a retry is a new run


def test_finishing_clears_the_callback_secret() -> None:
    run = Run(status="running", callback_secret=SECRET)
    now = datetime.now(UTC)
    change = apply_transition(run, RunStatus.FAILED, now=now, exit_reason="max_turns")
    assert (run.status, run.exit_reason, run.finished_at) == ("failed", "max_turns", now)
    assert run.callback_secret is None
    assert change.payload()["from"] == "running"
    with pytest.raises(InvalidTransition):
        apply_transition(run, RunStatus.RUNNING, now=now)


# ------------------------------------------------------------------ the sandbox callback


async def test_callback_rejects_bad_signatures(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    run = await make_run(session, await make_ticket(session))
    r = await callback(client, run.id, [{"kind": "heartbeat"}], secret="wrong")
    assert r.status_code == 401
    unsigned = await client.post(f"/api/internal/runs/{run.id}/events", json={"events": []})
    assert unsigned.status_code == 401
    assert (await callback(client, uuid.uuid4(), [])).status_code == 404


async def test_callback_for_a_finished_run_is_gone(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    run = await make_run(session, await make_ticket(session), status="cancelled")
    assert (await callback(client, run.id, [{"kind": "heartbeat"}])).status_code == 410


async def test_heartbeat_touches_the_run_but_not_the_transcript(
    client: httpx.AsyncClient, session: AsyncSession, publisher: FakePublisher
) -> None:
    run = await make_run(session, await make_ticket(session), status="running")
    before = run.last_heartbeat
    assert (await callback(client, run.id, [{"kind": "heartbeat"}])).status_code == 202
    await session.refresh(run)
    assert run.last_heartbeat and before and run.last_heartbeat > before
    assert (await session.scalars(select(RunEvent))).all() == []
    # ...and tells the board, which would otherwise show "No heartbeat" after 30 s.
    [state] = [d for _, e, d in publisher.events if e == "run.state"]
    assert state["run"]["last_heartbeat"] == run.last_heartbeat.isoformat()


async def test_running_state_moves_claimed_to_running_and_comments(
    client: httpx.AsyncClient,
    session: AsyncSession,
    comments: respx.Route,
    publisher: FakePublisher,
) -> None:
    run = await make_run(session, await make_ticket(session))
    r = await callback(client, run.id, [{"kind": "state", "payload": {"status": "running"}}])
    assert r.status_code == 202
    await session.refresh(run)
    assert run.status == "running"
    assert (
        "started working on `nextix/issue-7`"
        in json.loads(comments.calls[0].request.content)["body"]
    )
    assert any(e == "run.state" for _, e, _ in publisher.events)


async def test_transcript_events_are_redacted_truncated_and_published(
    client: httpx.AsyncClient, session: AsyncSession, publisher: FakePublisher
) -> None:
    run = await make_run(session, await make_ticket(session), status="running")
    leaky = "token ghs_" + "A" * 36 + " and sk-ant-oat01-" + "b" * 40
    events = [
        {"kind": "message", "payload": {"text": leaky}},
        {"kind": "tool_result", "payload": {"tool_use_id": "t1", "content": "x" * 50_000}},
        {"kind": "usage", "payload": {"input_tokens": 900, "output_tokens": 80, "cost_usd": 0.42}},
        {"kind": "usage", "payload": {"input_tokens": 100, "output_tokens": 5, "cost_usd": 0.1}},
    ]
    r = await callback(client, run.id, events)
    assert r.json() == {"stored": 4}

    rows = (await session.scalars(select(RunEvent).order_by(RunEvent.id))).all()
    text = rows[0].payload["text"]
    assert "ghs_A" not in text and "sk-ant-oat01" not in text and "[redacted]" in text
    assert len(rows[1].payload["content"]) <= TOOL_RESULT_LIMIT + 200
    await session.refresh(run)
    # usage is cumulative: a smaller, out-of-order report never lowers the totals
    assert (run.input_tokens, run.output_tokens, float(run.cost_usd)) == (900, 80, 0.42)
    channels = {c for c, _, _ in publisher.events}
    assert f"nextix:run:{run.id}" in channels


async def test_oversized_batches_are_refused(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    run = await make_run(session, await make_ticket(session), status="running")
    r = await callback(client, run.id, [{"kind": "log", "payload": {"text": "."}}] * 501)
    assert r.status_code == 413


# ------------------------------------------------------------------ queueing, retry, cancel


async def test_enqueue_numbers_attempts_and_refuses_a_second_active_run(
    session: AsyncSession,
    gh: GitHubClient,
    publisher: FakePublisher,
    enqueuer: FakeEnqueuer,
    comments: respx.Route,
) -> None:
    ticket = await make_ticket(session)
    await make_run(session, ticket, status="failed", attempt=1)
    run = await enqueue_run(
        session, ticket, trigger="retry", gh=gh, publisher=publisher, enqueue=enqueuer
    )
    assert (run.attempt, run.status, run.branch) == (2, "queued", "nextix/issue-7")
    assert enqueuer.run_ids == [run.id]
    assert "Queued" in json.loads(comments.calls[0].request.content)["body"]
    with pytest.raises(ActiveRunExists):
        await enqueue_run(
            session, ticket, trigger="retry", gh=gh, publisher=publisher, enqueue=enqueuer
        )


async def test_a_run_the_queue_rejects_is_cancelled_not_left_queued(
    session: AsyncSession,
    gh: GitHubClient,
    publisher: FakePublisher,
    enqueuer: FakeEnqueuer,
    comments: respx.Route,
) -> None:
    ticket = await make_ticket(session)
    enqueuer.fail = True
    with pytest.raises(ConnectionError):
        await enqueue_run(
            session, ticket, trigger="initial", gh=gh, publisher=publisher, enqueue=enqueuer
        )
    [run] = (await session.scalars(select(Run))).all()
    assert (run.status, run.exit_reason) == ("cancelled", "enqueue_failed")


async def test_retry_and_cancel_endpoints(
    client: httpx.AsyncClient, session: AsyncSession, enqueuer: FakeEnqueuer, comments: respx.Route
) -> None:
    ticket = await make_ticket(session)
    await make_run(session, ticket, status="failed")

    r = await client.post(f"/api/tickets/{ticket.id}/runs", headers=AUTH)
    assert r.status_code == 201, r.text
    new = r.json()
    assert (new["attempt"], new["status"], new["trigger"]) == (2, "queued", "retry")
    again = await client.post(f"/api/tickets/{ticket.id}/runs", headers=AUTH)
    assert again.status_code == 409

    c = await client.post(f"/api/runs/{new['id']}/cancel", headers=AUTH)
    assert c.status_code == 202 and c.json()["status"] == "cancelled"
    assert (await client.post(f"/api/runs/{new['id']}/cancel", headers=AUTH)).status_code == 409


async def test_retry_on_a_closed_issue_is_refused(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ticket = await make_ticket(session, state="closed")
    assert (await client.post(f"/api/tickets/{ticket.id}/runs", headers=AUTH)).status_code == 409


async def test_run_endpoints_require_the_token(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"/api/tickets/{uuid.uuid4()}")).status_code == 401
    assert (await client.post(f"/api/runs/{uuid.uuid4()}/cancel")).status_code == 401


# ------------------------------------------------------------------ reads


async def test_ticket_detail_and_event_paging(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ticket = await make_ticket(session)
    await make_run(session, ticket, status="failed", attempt=1)
    run = await make_run(session, ticket, status="running", attempt=2)
    session.add_all(
        [RunEvent(run_id=run.id, kind="log", payload={"text": f"line {i}"}) for i in range(5)]
    )
    await session.commit()

    detail = (await client.get(f"/api/tickets/{ticket.id}", headers=AUTH)).json()
    assert [r["attempt"] for r in detail["runs"]] == [2, 1]
    assert detail["runs"][0]["agent_id"] == "agent-abc123"

    page = (await client.get(f"/api/runs/{run.id}/events?limit=3", headers=AUTH)).json()
    assert [e["payload"]["text"] for e in page["events"]] == ["line 0", "line 1", "line 2"]
    rest = (
        await client.get(f"/api/runs/{run.id}/events?after={page['next_after']}", headers=AUTH)
    ).json()
    assert [e["payload"]["text"] for e in rest["events"]] == ["line 3", "line 4"]
    assert rest["next_after"] is None


async def test_queueing_a_run_takes_the_ticket_out_of_needs_input(
    session: AsyncSession,
    gh: GitHubClient,
    publisher: FakePublisher,
    enqueuer: FakeEnqueuer,
    comments: respx.Route,
    respx_mock: respx.MockRouter,
) -> None:
    ticket = await make_ticket(session)
    ticket.labels = ["nextix", "nextix:needs-input"]
    await session.commit()
    removed = respx_mock.delete("/repos/acme/widgets/issues/7/labels/nextix%3Aneeds-input").respond(
        200, json=[]
    )
    await enqueue_run(
        session, ticket, trigger="retry", gh=gh, publisher=publisher, enqueue=enqueuer
    )
    assert removed.called
    await session.refresh(ticket)
    assert ticket.labels == ["nextix"]


# ------------------------------------------------------------------ review fixes


async def test_a_resent_batch_is_stored_once(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    run = await make_run(session, await make_ticket(session), status="running")

    async def send(batch: int) -> httpx.Response:
        body = json.dumps(
            {"batch": batch, "events": [{"kind": "log", "payload": {"text": f"b{batch}"}}]}
        ).encode()
        return await client.post(
            f"/api/internal/runs/{run.id}/events",
            content=body,
            headers={"X-Nextix-Signature": sign(SECRET, body)},
        )

    assert (await send(1)).json() == {"stored": 1}
    assert (await send(1)).json() == {"stored": 0, "duplicate": True}  # the runner retried
    assert (await send(2)).json() == {"stored": 1}
    texts = [e.payload["text"] for e in (await session.scalars(select(RunEvent))).all()]
    assert texts == ["b1", "b2"]


async def test_nul_characters_do_not_sink_a_batch(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    run = await make_run(session, await make_ticket(session), status="running")
    r = await callback(client, run.id, [{"kind": "message", "payload": {"text": "a\u0000b"}}])
    assert r.status_code == 202
    [row] = (await session.scalars(select(RunEvent))).all()
    assert row.payload["text"] == "ab"


async def test_an_oversized_body_is_refused_before_it_is_read(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    run = await make_run(session, await make_ticket(session), status="running")
    r = await client.post(
        f"/api/internal/runs/{run.id}/events",
        content=b"x" * (5 * 1024 * 1024 + 1),
        headers={"X-Nextix-Signature": "sha256=0"},
    )
    assert r.status_code == 413


async def test_a_cancel_that_lands_mid_callback_is_not_undone(
    client: httpx.AsyncClient,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    comments: respx.Route,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nextix.api.internal as internal

    run = await make_run(session, await make_ticket(session))  # claimed
    original = internal.store_events

    async def cancel_right_after_storing(*args: Any, **kwargs: Any) -> Any:
        stored = await original(*args, **kwargs)
        async with session_factory() as other:
            row = await other.get(Run, run.id)
            assert row is not None
            row.status, row.finished_at = "cancelled", datetime.now(UTC)
            await other.commit()
        return stored

    monkeypatch.setattr(internal, "store_events", cancel_right_after_storing)
    r = await callback(client, run.id, [{"kind": "state", "payload": {"status": "running"}}])
    assert r.status_code == 202
    await session.refresh(run)
    assert run.status == "cancelled"


async def test_losing_the_enqueue_race_raises_active_run_exists(
    session: AsyncSession,
    gh: GitHubClient,
    publisher: FakePublisher,
    enqueuer: FakeEnqueuer,
    comments: respx.Route,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nextix.runs.lifecycle as lifecycle

    ticket = await make_ticket(session)
    await make_run(session, ticket, status="running")
    real = lifecycle.active_run
    calls = 0

    async def misses_it_once(s: AsyncSession, ticket_id: uuid.UUID) -> Run | None:
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real(s, ticket_id)  # the other request won

    monkeypatch.setattr(lifecycle, "active_run", misses_it_once)
    with pytest.raises(ActiveRunExists):
        await enqueue_run(
            session, ticket, trigger="retry", gh=gh, publisher=publisher, enqueue=enqueuer
        )
    assert enqueuer.run_ids == []


def test_run_time_limits_leave_room_for_the_agent_and_the_push() -> None:
    from nextix.api.deps import run_time_limits

    soft, hard = run_time_limits(90)
    assert soft > 90 * 60 + 120 and hard > soft


async def test_webhook_runs_use_the_issue_as_the_labeler_saw_it(
    session: AsyncSession,
    gh: GitHubClient,
    publisher: FakePublisher,
    enqueuer: FakeEnqueuer,
    comments: respx.Route,
) -> None:
    ticket = await make_ticket(session)
    run = await enqueue_run(
        session,
        ticket,
        trigger="initial",
        gh=gh,
        publisher=publisher,
        enqueue=enqueuer,
        task_title="Approved title",
        task_body="Approved body",
    )
    assert (run.task_title, run.task_body) == ("Approved title", "Approved body")
