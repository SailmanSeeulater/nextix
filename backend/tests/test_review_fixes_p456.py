"""Fixes from the Phase 4-6 review: pending reviews, retried feedback runs, fair per-repo
order, per-runner sandbox sweeps, NUL in commands."""

import socket
from datetime import UTC, datetime, timedelta

import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.db.models import Run, Ticket
from nextix.github.client import GitHubClient
from nextix.runs.config import ConfigError, parse_config
from nextix.runs.executor import RepoBusy, execute_run, sandbox_owner, sweep_orphans
from nextix.runs.lifecycle import enqueue_run, start_pending_reviews
from tests.conftest import FakeEnqueuer, FakePublisher
from tests.test_executor import SUCCESS, FakeSandbox, another_ticket, ctx, queued_run
from tests.test_executor import github as github

REVIEW = "/repos/acme/widgets/pulls/12/reviews/88"


async def ticket_with_pr(session: AsyncSession, run: Run) -> Ticket:
    ticket = await session.get(Ticket, run.ticket_id)
    assert ticket is not None
    ticket.pr_number, ticket.pr_state, ticket.pending_review_id = 12, "open", 88
    await session.commit()
    return ticket


async def test_a_pending_review_starts_once_the_run_is_gone_however_it_ended(
    session: AsyncSession,
    gh: GitHubClient,
    publisher: FakePublisher,
    enqueuer: FakeEnqueuer,
    github: dict[str, respx.Route],
    respx_mock: respx.MockRouter,
) -> None:
    run = await queued_run(session, status="cancelled")  # e.g. cancelled while queued
    ticket = await ticket_with_pr(session, run)
    route = respx_mock.get(REVIEW).respond(502)
    assert await start_pending_reviews(session, gh=gh, publisher=publisher, enqueue=enqueuer) == []
    await session.refresh(ticket)
    assert ticket.pending_review_id == 88  # GitHub failed: kept for the next beat

    route.respond(200, json={"id": 88, "user": {"login": "acme"}, "state": "CHANGES_REQUESTED"})
    [started] = await start_pending_reviews(session, gh=gh, publisher=publisher, enqueue=enqueuer)
    follow_up = await session.get(Run, started)
    assert follow_up is not None and (follow_up.trigger, follow_up.review_id) == (
        "review_feedback",
        88,
    )
    await session.refresh(ticket)
    assert ticket.pending_review_id is None


async def test_a_pending_review_waits_while_a_run_is_active(
    session: AsyncSession,
    gh: GitHubClient,
    publisher: FakePublisher,
    enqueuer: FakeEnqueuer,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session, status="running")
    await ticket_with_pr(session, run)
    assert await start_pending_reviews(session, gh=gh, publisher=publisher, enqueue=enqueuer) == []
    assert enqueuer.run_ids == []


async def test_retrying_a_feedback_run_addresses_the_same_review(
    session: AsyncSession,
    gh: GitHubClient,
    publisher: FakePublisher,
    enqueuer: FakeEnqueuer,
    github: dict[str, respx.Route],
) -> None:
    failed = await queued_run(session, status="failed")
    failed.trigger, failed.review_id = "review_feedback", 77
    failed.review_meta = {"id": 77, "author": "acme"}
    failed.task_title, failed.task_body = "Approved title", "Approved body"
    ticket = await session.get(Ticket, failed.ticket_id)
    assert ticket is not None
    await session.commit()

    run = await enqueue_run(
        session, ticket, trigger="retry", gh=gh, publisher=publisher, enqueue=enqueuer
    )
    assert (run.trigger, run.review_id, run.task_title) == (
        "review_feedback",
        77,
        "Approved title",
    )


async def test_an_older_queued_run_for_the_repo_goes_first(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    older = await queued_run(session)
    older.queued_at = datetime.now(UTC) - timedelta(minutes=2)
    newer = Run(ticket_id=(await another_ticket(session, older, 8)).id, attempt=1, status="queued")
    session.add(newer)
    await session.commit()
    sandbox = FakeSandbox(SUCCESS)
    with pytest.raises(RepoBusy):
        await execute_run(newer.id, ctx(session_factory, gh, publisher, sandbox))
    assert sandbox.specs == []


async def test_another_runners_sandbox_is_left_alone(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    theirs = await queued_run(session, status="running")
    sandbox = FakeSandbox()
    sandbox.labelled = {theirs.id}
    sandbox.owners = {theirs.id: "another-host/0ldb00t:1"}
    removed = await sweep_orphans(session, ctx(session_factory, gh, publisher, sandbox))
    assert removed == set() and sandbox.killed_runs == []
    assert sandbox_owner().startswith(f"{socket.gethostname()}/")


def test_commands_may_not_contain_nul() -> None:
    with pytest.raises(ConfigError, match=r"setup.0"):
        parse_config('setup:\n  - "npm ci\\0"\n')
