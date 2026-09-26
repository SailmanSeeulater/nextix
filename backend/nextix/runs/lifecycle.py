"""Queueing runs and applying status changes with all of their side effects.

Every status change goes through `record_transition`, which validates it with the state
machine, writes a `run_events` row, posts a short comment on the GitHub issue (the issue
timeline doubles as the audit log), and publishes board and run events.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.db.models import Repo, Run, Ticket
from nextix.events.stream import BOARD_CHANNEL, EventPublisher, publish_ticket_changes
from nextix.github.client import GitHubClient
from nextix.redact import redact
from nextix.runs.events import run_channel, store_events
from nextix.runs.state import ACTIVE_STATUSES, RunStatus, apply_transition
from nextix.tickets.service import BRANCH_PREFIX, NEEDS_INPUT_LABEL

log = logging.getLogger(__name__)


class RunEnqueuer(Protocol):
    """Hands a queued run to the worker (Celery in production, a list in tests)."""

    def __call__(self, run_id: uuid.UUID) -> None: ...


class ActiveRunExists(Exception):
    def __init__(self, run: Run) -> None:
        super().__init__(f"ticket already has an active run ({run.status})")
        self.run = run


def run_detail(run: Run) -> dict[str, Any]:
    """The RunDetail shape (docs/phase3.md, phase4.md). Artifacts are added by the
    ticket detail endpoint, which loads them for every run at once."""

    def iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None

    return {
        "id": str(run.id),
        "attempt": run.attempt,
        "trigger": run.trigger,
        "status": run.status,
        "agent_id": run.agent_id,
        "branch": run.branch,
        "queued_at": iso(run.queued_at),
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "last_heartbeat": iso(run.last_heartbeat),
        "exit_reason": run.exit_reason,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "cost_usd": float(run.cost_usd),
        "summary": run.summary,
        "question": run.question,
        "tests": run.tests,
        "review_errors": run.review_errors or [],
        "review": run.review_meta,
    }


async def active_run(session: AsyncSession, ticket_id: uuid.UUID) -> Run | None:
    return await session.scalar(
        select(Run).where(Run.ticket_id == ticket_id, Run.status.in_(ACTIVE_STATUSES))
    )


def _comment_for(run: Run, status: str, exit_reason: str | None) -> str | None:
    agent = run.agent_id or "an agent"
    reasons = {
        "heartbeat_lost": "the agent stopped reporting (no heartbeat for 90 s)",
        "no_changes": "the agent finished without changing any files",
        "max_turns": "the agent used all of its turns",
        "max_cost": "the run reached its cost limit",
        "usage_limit": "your Claude plan's usage limit was reached",
        "sandbox_error": "the sandbox could not start or crashed",
        "push_failed": "pushing the branch failed",
        "timeout": "the run hit its time limit",
        "clone_failed": "the sandbox could not clone the repository",
        "auth_failed": (
            "Claude rejected the credential (check CLAUDE_CODE_OAUTH_TOKEN or "
            "ANTHROPIC_API_KEY in .env)"
        ),
        "agent_error": "the agent stopped with an error (see the transcript)",
        "runner_error": "the sandbox runner crashed (see the transcript)",
        "no_claude_credentials": "no Claude credentials are configured on the server",
        "enqueue_failed": "the run could not be handed to a worker",
        "empty_repo": "the repository has no commits to branch from",
        "worker_restarted": "the runner restarted mid-run, so nobody was watching this run",
        "secret_in_changes": (
            "the agent's changes contained one of the run's credentials, so nothing was pushed"
        ),
    }
    reason = reasons.get(exit_reason or "", exit_reason or "unknown reason")
    if status == RunStatus.QUEUED:
        return f"🕒 Queued for an agent (attempt {run.attempt})."
    if status == RunStatus.CLAIMED:
        return f"🤖 Picked up by {agent}."
    if status == RunStatus.RUNNING:
        return f"▶️ {agent} started working on `{run.branch}`."
    if status == RunStatus.FAILED:
        return f"❌ Failed: {reason}."
    if status == RunStatus.TIMED_OUT:
        return f"⏱️ Timed out: {reason}."
    if status == RunStatus.CANCELLED:
        return "🛑 Run cancelled."
    return None  # succeeded and needs_input post their own, richer comments


async def _comment(gh: GitHubClient, repo: Repo, ticket: Ticket, body: str) -> None:
    try:
        await gh.create_comment(
            repo.installation_id, repo.owner, repo.name, ticket.issue_number, body
        )
    except Exception:  # a comment failure must never wedge the run
        log.exception("could not comment on %s#%s", repo.full_name, ticket.issue_number)


async def publish_run(publisher: EventPublisher, run: Run) -> None:
    detail = run_detail(run)
    for channel, event, data in (
        (run_channel(run.id), "run.updated", detail),
        (BOARD_CHANNEL, "run.state", {"ticket_id": str(run.ticket_id), "run": detail}),
    ):
        try:
            await publisher.publish(channel, event, data)
        except Exception:
            log.exception("could not publish %s for run %s", event, run.id)


async def record_transition(
    session: AsyncSession,
    run: Run,
    target: str,
    *,
    gh: GitHubClient,
    publisher: EventPublisher,
    exit_reason: str | None = None,
    comment: str | None = None,
    now: datetime | None = None,
) -> None:
    """Apply a status change, persist it, and announce it. Raises InvalidTransition."""
    change = apply_transition(run, target, now=now or datetime.now(UTC), exit_reason=exit_reason)
    # Commits the status change together with its transcript event.
    await store_events(session, publisher, run.id, [("state", change.payload())])

    ticket = await session.get(Ticket, run.ticket_id)
    repo = await session.get(Repo, ticket.repo_id) if ticket else None
    if ticket and repo:
        text = comment if comment is not None else _comment_for(run, target, exit_reason)
        if text:
            await _comment(gh, repo, ticket, redact(text))
    await publish_run(publisher, run)
    await publish_ticket_changes(session, publisher, {run.ticket_id})


async def _clear_needs_input(
    session: AsyncSession, gh: GitHubClient, repo: Repo, ticket: Ticket
) -> None:
    """A new run means the question was answered: take the ticket out of Needs Input."""
    try:
        await gh.remove_label(
            repo.installation_id, repo.owner, repo.name, ticket.issue_number, NEEDS_INPUT_LABEL
        )
    except Exception:
        log.exception(
            "could not remove %s from %s#%s", NEEDS_INPUT_LABEL, repo.full_name, ticket.issue_number
        )
        return
    ticket.labels = [label for label in ticket.labels if label != NEEDS_INPUT_LABEL]
    await session.commit()


async def enqueue_run(
    session: AsyncSession,
    ticket: Ticket,
    *,
    trigger: str,
    gh: GitHubClient,
    publisher: EventPublisher,
    enqueue: RunEnqueuer,
    task_title: str | None = None,
    task_body: str | None = None,
    review_id: int | None = None,
    review_meta: dict[str, Any] | None = None,
) -> Run:
    """Create a queued run for `ticket` and hand it to the worker.

    The run keeps a snapshot of the task (`task_title`/`task_body`, default: the ticket as
    it is now): the agent works from what the trusted person approved, not from whatever
    the issue says by the time the run starts.

    Raises ActiveRunExists if the ticket already has a queued/claimed/running run (the
    database's partial unique index backs this up against races).
    """
    ticket_id = ticket.id
    existing = await active_run(session, ticket_id)
    if existing:
        raise ActiveRunExists(existing)
    attempt = (
        await session.scalar(
            select(func.coalesce(func.max(Run.attempt), 0)).where(Run.ticket_id == ticket.id)
        )
        or 0
    ) + 1
    run = Run(
        ticket_id=ticket.id,
        attempt=attempt,
        trigger=trigger,
        status=RunStatus.QUEUED,
        branch=f"{BRANCH_PREFIX}{ticket.issue_number}",
        queued_at=datetime.now(UTC),
        task_title=ticket.title if task_title is None else task_title,
        task_body=(ticket.body or "") if task_body is None else task_body,
        review_id=review_id,
        review_meta=review_meta,
    )
    try:
        # A savepoint, so losing a race rolls back only this insert and nothing else in
        # the session is expired.
        async with session.begin_nested():
            session.add(run)
    except IntegrityError as exc:
        existing = await active_run(session, ticket_id)
        if existing:
            raise ActiveRunExists(existing) from exc
        raise
    await store_events(
        session, publisher, run.id, [("state", {"from": None, "status": RunStatus.QUEUED})]
    )

    repo = await session.get(Repo, ticket.repo_id)
    if repo:
        text = _comment_for(run, RunStatus.QUEUED, None)
        reviewer = (review_meta or {}).get("author")
        if text and reviewer:
            text = (
                f"🕒 Queued for an agent (attempt {run.attempt}) to address @{reviewer}'s review."
            )
        if text:
            await _comment(gh, repo, ticket, text)
        if NEEDS_INPUT_LABEL in ticket.labels:
            await _clear_needs_input(session, gh, repo, ticket)
    try:
        enqueue(run.id)
    except Exception:
        # A queued run nobody will pick up would block retries forever; cancel it instead.
        log.exception("could not hand run %s to the queue", run.id)
        await record_transition(
            session,
            run,
            RunStatus.CANCELLED,
            gh=gh,
            publisher=publisher,
            exit_reason="enqueue_failed",
            comment="⚠️ The run could not be handed to a worker (is Redis up?). "
            "Retry it from the board.",
        )
        raise
    await publish_run(publisher, run)
    await publish_ticket_changes(session, publisher, {ticket.id})
    return run


async def start_pending_review(
    session: AsyncSession,
    ticket_id: uuid.UUID,
    *,
    gh: GitHubClient,
    publisher: EventPublisher,
    enqueue: RunEnqueuer,
) -> Run | None:
    """Queue the feedback run for a review that arrived while another run was active."""
    ticket = await session.get(Ticket, ticket_id, populate_existing=True)
    if ticket is None or ticket.pending_review_id is None:
        return None
    review_id = ticket.pending_review_id
    ticket.pending_review_id = None
    await session.commit()
    repo = await session.get(Repo, ticket.repo_id)
    if repo is None or ticket.pr_number is None or ticket.pr_state != "open":
        return None
    if ticket.issue_state != "open":
        return None
    try:
        review = await gh.get_review(
            repo.installation_id, repo.owner, repo.name, ticket.pr_number, review_id
        )
    except Exception:
        log.exception("could not read pending review %s", review_id)
        return None
    latest = await session.scalar(
        select(Run).where(Run.ticket_id == ticket.id).order_by(Run.attempt.desc()).limit(1)
    )
    meta = {
        "id": review.id,
        "author": review.user.login if review.user else None,
        "state": review.state.lower(),
        "html_url": review.html_url,
        "comments": None,
    }
    try:
        return await enqueue_run(
            session,
            ticket,
            trigger="review_feedback",
            gh=gh,
            publisher=publisher,
            enqueue=enqueue,
            task_title=latest.task_title if latest else None,
            task_body=latest.task_body if latest else None,
            review_id=review.id,
            review_meta=meta,
        )
    except ActiveRunExists:
        ticket.pending_review_id = review_id  # still busy; try again after that run
        await session.commit()
        return None
