"""Running one agent run end to end (docs/phase3.md, "Run lifecycle").

claim → sandbox → wait (watching for exit, cancel, reaper, timeout) → read the result →
push the branch and open/update the PR, or record needs-input / failure.

Everything external sits behind interfaces on `WorkerContext` (GitHub, the sandbox, the
pusher, the publisher), so the whole flow is testable with fakes.
"""

import asyncio
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.claude_auth import ClaudeAuth, resolve
from nextix.config import Settings
from nextix.db.models import Repo, Run, Ticket
from nextix.events.stream import EventPublisher, publish_ticket_changes
from nextix.github.client import GitHubClient
from nextix.redact import redact
from nextix.runs.lifecycle import record_transition
from nextix.runs.push import BranchPusher, PushError
from nextix.runs.sandbox import BUNDLE_PATH, RESULT_PATH, Sandbox, SandboxSpec
from nextix.runs.state import ACTIVE_STATUSES, TERMINAL_STATUSES, RunStatus
from nextix.tickets.service import NEEDS_INPUT_LABEL, is_trusted

log = logging.getLogger(__name__)

# Both far below Linux's 128 KiB limit on one environment variable (even at 4 bytes a
# character) and far above any real issue.
MAX_ISSUE_BODY_CHARS = 20_000
MAX_CLARIFICATION_CHARS = 8_000


class RunResult(BaseModel):
    """/work/.nextix-out/result.json, written by the sandbox runner."""

    model_config = ConfigDict(extra="ignore")

    status: str
    summary: str = ""
    question: str | None = None
    commits: int = 0
    exit_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0


@dataclass
class WorkerContext:
    sessions: async_sessionmaker[AsyncSession]
    gh: GitHubClient
    publisher: EventPublisher
    sandbox: Sandbox
    pusher: BranchPusher
    settings: Settings
    poll_interval_s: float = 2.0
    grace_s: float = 120.0


def claude_credential_env(settings: Settings) -> dict[str, str]:
    """Exactly one Claude credential for the sandbox, per NEXTIX_CLAUDE_AUTH."""
    mode = resolve(settings)
    if mode is ClaudeAuth.SUBSCRIPTION:
        return {"CLAUDE_CODE_OAUTH_TOKEN": settings.claude_code_oauth_token.strip()}
    if mode is ClaudeAuth.API_KEY:
        return {"ANTHROPIC_API_KEY": settings.anthropic_api_key.strip()}
    return {}


def sandbox_env(
    *,
    settings: Settings,
    run: Run,
    ticket: Ticket,
    repo: Repo,
    clone_token: str,
    clarification: str = "",
) -> dict[str, str]:
    """The complete environment contract from docs/phase3.md. Nothing else is passed.

    The task is the run's snapshot (what the trusted person approved), not the live issue.
    """
    title = run.task_title if run.task_title is not None else ticket.title
    body = run.task_body if run.task_body is not None else (ticket.body or "")
    if len(body) > MAX_ISSUE_BODY_CHARS:
        body = body[:MAX_ISSUE_BODY_CHARS] + "\n\n… [the rest of the issue was cut: too long]"
    if clarification:
        clarification = clarification[:MAX_CLARIFICATION_CHARS]
        body = f"{body}\n\n{clarification}" if body else clarification
    task = {
        "title": title,
        "body": body,
        "extra_instructions": "",  # from .nextix.yml in Phase 4
        "review_comments": [],  # from PR reviews in Phase 5
    }
    return {
        "NEXTIX_RUN_ID": str(run.id),
        "NEXTIX_CALLBACK_URL": (
            f"{settings.agent_callback_base.rstrip('/')}/api/internal/runs/{run.id}/events"
        ),
        "NEXTIX_CALLBACK_SECRET": run.callback_secret or "",
        "NEXTIX_REPO": repo.full_name,
        "NEXTIX_DEFAULT_BRANCH": repo.default_branch,
        "NEXTIX_BRANCH": run.branch or f"nextix/issue-{ticket.issue_number}",
        "NEXTIX_ISSUE_NUMBER": str(ticket.issue_number),
        # Unescaped UTF-8: a single environment variable must stay under Linux's 128 KiB.
        "NEXTIX_TASK_JSON": json.dumps(task, ensure_ascii=False),
        "NEXTIX_MODEL": settings.anthropic_model,
        "NEXTIX_MAX_TURNS": str(settings.agent_max_turns),
        "NEXTIX_TIMEOUT_MIN": str(settings.agent_default_timeout_min),
        "NEXTIX_MAX_COST_USD": str(settings.agent_default_max_cost_usd),
        "NEXTIX_ALLOWED_TOOLS": settings.agent_allowed_tools,
        "GITHUB_TOKEN": clone_token,
        **claude_credential_env(settings),
    }


def pr_body(*, settings: Settings, run: Run, ticket: Ticket, result: RunResult) -> str:
    link = f"{settings.nextix_public_url.rstrip('/')}/tickets/{ticket.id}"
    duration = ""
    if run.started_at:
        seconds = int((datetime.now(UTC) - run.started_at).total_seconds())
        duration = f" · {seconds // 60}m {seconds % 60:02d}s"
    summary = redact(result.summary.strip()) or "_The agent did not leave a summary._"
    tokens = result.input_tokens + result.output_tokens
    return (
        f"{summary}\n\n"
        f"Closes #{ticket.issue_number}\n\n"
        f"[Agent transcript on the nexTix board]({link})\n\n"
        "---\n"
        f"<sub>🤖 nexTix · {run.agent_id} · attempt {run.attempt} · "
        f"${result.cost_usd:.2f} · {tokens:,} tokens{duration}</sub>\n"
    )


async def clarification_for(
    session: AsyncSession, run: Run, ticket: Ticket, repo: Repo, ctx: WorkerContext
) -> str:
    """The last open question on this ticket and the trusted people's replies to it.

    The question is the one an earlier run asked, or, before any run asked one, the one
    triage asked when the ticket was filed. Replies are issue comments from the repo owner
    or NEXTIX_ALLOWED_GITHUB_USERS posted after it; anyone else's comments are left out.
    """
    asked_run = await session.scalar(
        select(Run)
        .where(Run.ticket_id == ticket.id, Run.id != run.id, Run.question.is_not(None))
        .order_by(Run.attempt.desc())
        .limit(1)
    )
    if asked_run is not None and asked_run.question:
        question, asked_at, who = asked_run.question, asked_run.finished_at, "A previous attempt"
    elif ticket.triage_question:
        question, asked_at, who = ticket.triage_question, ticket.created_at, "Triage"
    else:
        return ""
    try:
        comments = await ctx.gh.list_issue_comments(
            repo.installation_id,
            repo.owner,
            repo.name,
            ticket.issue_number,
            since=asked_at,
        )
    except Exception:
        log.exception("could not read the answers on %s#%s", repo.full_name, ticket.issue_number)
        comments = []
    allowed = ctx.settings.allowed_github_users
    answers = [
        f"@{c.user.login}: {c.body.strip()}"
        for c in comments
        if c.user
        and c.body
        and c.body.strip()
        and is_trusted(c.user.login, repo, allowed)
        and (asked_at is None or c.created_at >= asked_at)
    ]
    reply = (
        "\n\n".join(answers)
        if answers
        else "(No reply comment. The owner may have answered by editing the description above.)"
    )
    return (
        "## Earlier question and answer\n\n"
        f"{who} stopped to ask:\n\n{question}\n\n"
        f"The answer:\n\n{reply}"
    )


async def _load(session: AsyncSession, run_id: uuid.UUID) -> tuple[Run, Ticket, Repo] | None:
    run = await session.get(Run, run_id, populate_existing=True)
    if run is None:
        return None
    ticket = await session.get(Ticket, run.ticket_id)
    repo = await session.get(Repo, ticket.repo_id) if ticket else None
    if ticket is None or repo is None:
        return None
    return run, ticket, repo


async def _claim(session: AsyncSession, run_id: uuid.UUID, ctx: WorkerContext) -> Run | None:
    """queued → claimed, atomically. None if someone else has it or it's no longer queued."""
    run = await session.scalar(
        select(Run).where(Run.id == run_id).with_for_update(skip_locked=True)
    )
    if run is None or run.status != RunStatus.QUEUED:
        await session.rollback()
        return None
    run.agent_id = f"agent-{secrets.token_hex(3)}"
    run.callback_secret = secrets.token_hex(32)
    await record_transition(session, run, RunStatus.CLAIMED, gh=ctx.gh, publisher=ctx.publisher)
    return run


async def _finish(
    session: AsyncSession,
    run: Run,
    target: str,
    ctx: WorkerContext,
    *,
    exit_reason: str | None = None,
    comment: str | None = None,
) -> None:
    """Move to a terminal state, passing through running if the runner never reported in."""
    # Row lock: the reaper or a cancel may be finishing the same run concurrently.
    await session.refresh(run, with_for_update=True)
    if run.status in TERMINAL_STATUSES:
        await session.rollback()
        return  # cancelled by the owner or failed by the reaper meanwhile
    if run.status == RunStatus.CLAIMED and target not in (RunStatus.FAILED, RunStatus.CANCELLED):
        await record_transition(
            session, run, RunStatus.RUNNING, gh=ctx.gh, publisher=ctx.publisher, comment=""
        )
        await session.refresh(run, with_for_update=True)
        if run.status in TERMINAL_STATUSES:
            await session.rollback()
            return
    await record_transition(
        session,
        run,
        target,
        gh=ctx.gh,
        publisher=ctx.publisher,
        exit_reason=exit_reason,
        comment=comment,
    )


async def _wait(session: AsyncSession, run: Run, container_id: str, ctx: WorkerContext) -> str:
    """Wait for the sandbox. Returns 'exited', 'timeout', or 'stopped' (cancel/reaper)."""
    deadline = time.monotonic() + ctx.settings.agent_default_timeout_min * 60 + ctx.grace_s
    while True:
        if not await asyncio.to_thread(ctx.sandbox.is_running, container_id):
            return "exited"
        status = await session.scalar(select(Run.status).where(Run.id == run.id))
        await session.commit()  # don't sit "idle in transaction" for half an hour
        if status in TERMINAL_STATUSES:
            await asyncio.to_thread(ctx.sandbox.kill, container_id)
            return "stopped"
        if time.monotonic() > deadline:
            await asyncio.to_thread(ctx.sandbox.kill, container_id)
            return "timeout"
        await asyncio.sleep(ctx.poll_interval_s)


KEEP_ALIVE_INTERVAL_S = 20.0


async def _keep_alive(ctx: WorkerContext, run_id: uuid.UUID) -> None:
    """Heartbeat on the sandbox's behalf while the worker publishes its work."""
    while True:
        try:
            async with ctx.sessions() as session:
                await session.execute(
                    update(Run)
                    .where(Run.id == run_id, Run.status.in_(ACTIVE_STATUSES))
                    .values(last_heartbeat=datetime.now(UTC))
                )
                await session.commit()
        except Exception:
            log.exception("could not refresh the heartbeat of run %s", run_id)
        await asyncio.sleep(KEEP_ALIVE_INTERVAL_S)


async def _still_active(session: AsyncSession, run: Run) -> bool:
    """Re-read the run under a row lock; False once it was cancelled or reaped."""
    await session.refresh(run, with_for_update=True)
    active = run.status not in TERMINAL_STATUSES
    await session.commit()
    return active


def _parse_result(raw: bytes | None) -> RunResult | None:
    if raw is None:
        return None
    try:
        return RunResult.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        log.warning("unreadable result.json")
        return None


async def sweep_orphans(session: AsyncSession, ctx: WorkerContext) -> set[uuid.UUID]:
    """Remove every sandbox left from before this run, e.g. after the runner restarted.

    There is one runner and it runs one agent at a time, so a sandbox that exists when a
    run starts has nobody watching it: its result would never be collected. It is removed,
    and its run, if still active, fails with `worker_restarted` so it can be retried.
    (With several runners this would need per-worker ownership; that's Phase 6.)
    """
    labelled = await asyncio.to_thread(ctx.sandbox.labelled_runs)
    for run_id in labelled:
        log.warning("removing the unsupervised sandbox of run %s", run_id)
        await asyncio.to_thread(ctx.sandbox.kill_run, run_id)
        run = await session.scalar(
            select(Run)
            .where(Run.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        # Nothing to change: commit (not roll back) to release the lock, so objects the
        # caller holds in this session aren't expired.
        if run is None or run.status in TERMINAL_STATUSES:
            await session.commit()
            continue
        if run.status == RunStatus.QUEUED:
            await session.commit()  # never started, so it isn't this sandbox's run
            continue
        await record_transition(
            session,
            run,
            RunStatus.FAILED,
            gh=ctx.gh,
            publisher=ctx.publisher,
            exit_reason="worker_restarted",
        )
    return labelled


async def _has_base_branch(repo: Repo, ctx: WorkerContext) -> bool:
    try:
        return await ctx.gh.branch_exists(
            repo.installation_id, repo.owner, repo.name, repo.default_branch
        )
    except Exception:
        log.exception("could not check %s:%s; trying anyway", repo.full_name, repo.default_branch)
        return True


async def execute_run(run_id: uuid.UUID, ctx: WorkerContext) -> None:
    async with ctx.sessions() as session:
        try:
            await sweep_orphans(session, ctx)
        except Exception:
            log.exception("could not sweep leftover sandboxes")
        run = await _claim(session, run_id, ctx)
        if run is None:
            log.info("run %s is not claimable; skipping", run_id)
            return
        loaded = await _load(session, run_id)
        assert loaded is not None
        run, ticket, repo = loaded

        if resolve(ctx.settings) is ClaudeAuth.NONE:
            await _finish(
                session,
                run,
                RunStatus.FAILED,
                ctx,
                exit_reason="no_claude_credentials",
                comment="❌ Failed: no Claude credentials are configured on the server.",
            )
            return

        if not await _has_base_branch(repo, ctx):
            await _finish(
                session,
                run,
                RunStatus.FAILED,
                ctx,
                exit_reason="empty_repo",
                comment=(
                    f"❌ Failed: `{repo.default_branch}` has no commits yet, so there is "
                    "nothing to branch from. Push a first commit (a README is enough), "
                    "then retry."
                ),
            )
            return

        container_id: str | None = None
        keep_alive: asyncio.Task[None] | None = None
        outcome = "exited"
        result: RunResult | None = None
        bundle: bytes | None = None
        try:
            clone_token = await ctx.gh.auth.scoped_token(
                repo.installation_id, repository=repo.name, permissions={"contents": "read"}
            )
            clarification = await clarification_for(session, run, ticket, repo, ctx)
            spec = SandboxSpec(
                run_id=run.id,
                image=ctx.settings.agent_image,
                env=sandbox_env(
                    settings=ctx.settings,
                    run=run,
                    ticket=ticket,
                    repo=repo,
                    clone_token=clone_token,
                    clarification=clarification,
                ),
                network=ctx.settings.agent_network or None,
                mem_limit=ctx.settings.agent_mem_limit,
                nano_cpus=int(ctx.settings.agent_cpus * 1_000_000_000),
                pids_limit=ctx.settings.agent_pids_limit,
            )
            container_id = await asyncio.to_thread(ctx.sandbox.start, spec)
            run.container_id = container_id
            await session.commit()
            outcome = await _wait(session, run, container_id, ctx)
            keep_alive = asyncio.create_task(_keep_alive(ctx, run.id))
            result = _parse_result(
                await asyncio.to_thread(ctx.sandbox.read_file, container_id, RESULT_PATH)
            )
            if result and result.commits > 0:
                bundle = await asyncio.to_thread(ctx.sandbox.read_file, container_id, BUNDLE_PATH)
        except Exception as exc:
            log.exception("sandbox for run %s failed", run_id)
            await _finish(
                session,
                run,
                RunStatus.FAILED,
                ctx,
                exit_reason="sandbox_error",
                comment=f"❌ Failed: the sandbox could not run ({redact(str(exc))[:200]}).",
            )
            if keep_alive:
                keep_alive.cancel()
            return
        finally:
            if container_id:
                await asyncio.to_thread(ctx.sandbox.remove, container_id)

        try:
            await _record_usage(session, run, result)
            await _conclude(
                session, run, ticket, repo, ctx, outcome=outcome, result=result, bundle=bundle
            )
        finally:
            if keep_alive:
                keep_alive.cancel()


async def _record_usage(session: AsyncSession, run: Run, result: RunResult | None) -> None:
    if result is None:
        return
    await session.refresh(run)
    run.input_tokens = max(run.input_tokens, result.input_tokens)
    run.output_tokens = max(run.output_tokens, result.output_tokens)
    run.cost_usd = max(run.cost_usd, Decimal(str(round(result.cost_usd, 4))))
    run.summary = redact(result.summary)[:4000] or None
    await session.commit()


async def _conclude(
    session: AsyncSession,
    run: Run,
    ticket: Ticket,
    repo: Repo,
    ctx: WorkerContext,
    *,
    outcome: str,
    result: RunResult | None,
    bundle: bytes | None,
) -> None:
    await session.refresh(run)
    if run.status in TERMINAL_STATUSES:
        return  # the owner cancelled, or the reaper already failed it and commented
    if result is None:
        if outcome == "timeout":
            minutes = ctx.settings.agent_default_timeout_min
            await _finish(
                session,
                run,
                RunStatus.TIMED_OUT,
                ctx,
                exit_reason="timeout",
                comment=f"⏱️ Timed out after {minutes}m; the sandbox was stopped.",
            )
        else:
            await _finish(session, run, RunStatus.FAILED, ctx, exit_reason="sandbox_error")
        return

    if result.status == "needs_input" and result.question:
        await _needs_input(session, run, ticket, repo, ctx, result.question)
        return
    if result.status == "timed_out":
        await _finish(session, run, RunStatus.TIMED_OUT, ctx, exit_reason="timeout")
        return
    if result.status != "succeeded":
        await _finish(
            session, run, RunStatus.FAILED, ctx, exit_reason=result.exit_reason or "agent_error"
        )
        return
    if result.commits <= 0 or not bundle:
        await _finish(session, run, RunStatus.FAILED, ctx, exit_reason="no_changes")
        return

    try:
        if not await _still_active(session, run):
            log.info("run %s ended before publishing; nothing pushed", run.id)
            return
        write_token = await ctx.gh.auth.scoped_token(
            repo.installation_id, repository=repo.name, permissions={"contents": "write"}
        )
        await asyncio.to_thread(
            ctx.pusher.push,
            repo_full_name=repo.full_name,
            token=write_token,
            branch=run.branch or "",
            default_branch=repo.default_branch,
            bundle=bundle,
        )
        if not await _still_active(session, run):
            log.info("run %s ended after its push; no pull request opened", run.id)
            return
        pr, created = await _open_or_update_pr(run, ticket, repo, ctx, result)
    except (PushError, Exception) as exc:
        log.exception("publishing run %s failed", run.id)
        await _finish(
            session,
            run,
            RunStatus.FAILED,
            ctx,
            exit_reason="push_failed",
            comment=f"❌ Failed: could not publish the branch ({redact(str(exc))[:200]}).",
        )
        return

    ticket = await session.get(Ticket, ticket.id, populate_existing=True) or ticket
    ticket.pr_number = pr["number"]
    ticket.pr_state = "open"
    ticket.updated_at = datetime.now(UTC)
    await session.commit()
    verb = "Opened" if created else "Updated"
    await _finish(
        session,
        run,
        RunStatus.SUCCEEDED,
        ctx,
        comment=(
            f"✅ {verb} PR #{pr['number']} from `{run.branch}` "
            f"(${result.cost_usd:.2f}, {result.num_turns} turns)."
        ),
    )


async def _open_or_update_pr(
    run: Run, ticket: Ticket, repo: Repo, ctx: WorkerContext, result: RunResult
) -> tuple[dict[str, Any], bool]:
    title = f"[nextix #{ticket.issue_number}] {ticket.title}"
    body = pr_body(settings=ctx.settings, run=run, ticket=ticket, result=result)
    existing = await ctx.gh.find_open_pull(
        repo.installation_id, repo.owner, repo.name, branch=run.branch or ""
    )
    if existing:
        pr = await ctx.gh.update_pull(
            repo.installation_id, repo.owner, repo.name, existing.number, title=title, body=body
        )
        return {"number": pr.number}, False
    pr = await ctx.gh.create_pull(
        repo.installation_id,
        repo.owner,
        repo.name,
        title=title,
        body=body,
        head=run.branch or "",
        base=repo.default_branch,
    )
    return {"number": pr.number}, True


async def _needs_input(
    session: AsyncSession, run: Run, ticket: Ticket, repo: Repo, ctx: WorkerContext, question: str
) -> None:
    ticket_id = ticket.id
    question = redact(question.strip())[:4000]
    await session.refresh(run, with_for_update=True)
    if run.status in TERMINAL_STATUSES:
        await session.rollback()
        return  # cancelled or reaped meanwhile: don't ask on its behalf
    run.question = question
    await session.commit()
    try:
        await ctx.gh.add_labels(
            repo.installation_id, repo.owner, repo.name, ticket.issue_number, [NEEDS_INPUT_LABEL]
        )
        ticket = await session.get(Ticket, ticket_id, populate_existing=True) or ticket
        if NEEDS_INPUT_LABEL not in ticket.labels:
            ticket.labels = [*ticket.labels, NEEDS_INPUT_LABEL]
            await session.commit()
    except Exception:
        log.exception("could not label %s#%s", repo.full_name, ticket.issue_number)
    await _finish(
        session,
        run,
        RunStatus.NEEDS_INPUT,
        ctx,
        comment=(
            f"🤖 **{run.agent_id} needs more information before it can continue.**\n\n"
            f"{question}\n\nReply to this issue with the answer."
        ),
    )
    await publish_ticket_changes(session, ctx.publisher, {ticket_id})
