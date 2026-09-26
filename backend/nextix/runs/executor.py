"""Running one agent run end to end (docs/phase3.md, "Run lifecycle").

claim → sandbox → wait (watching for exit, cancel, reaper, timeout) → read the result →
push the branch and open/update the PR, or record needs-input / failure.

Everything external sits behind interfaces on `WorkerContext` (GitHub, the sandbox, the
pusher, the publisher), so the whole flow is testable with fakes.
"""

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.claude_auth import ClaudeAuth, resolve
from nextix.config import Settings
from nextix.db.models import Artifact, Repo, Run, Ticket
from nextix.events.stream import EventPublisher, publish_ticket_changes
from nextix.github.client import GitHubClient
from nextix.redact import redact
from nextix.runs.artifacts import MAX_TOTAL_BYTES as MAX_ARTIFACT_BYTES
from nextix.runs.artifacts import SANDBOX_DIR as ARTIFACTS_SANDBOX_DIR
from nextix.runs.artifacts import collect as collect_artifacts
from nextix.runs.config import (
    CONFIG_PATH,
    ConfigError,
    NextixConfig,
    RunLimits,
    load_repo_config,
)
from nextix.runs.lifecycle import RunEnqueuer, record_transition, start_pending_review
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
    tests: dict[str, Any] | None = None


def clean_tests(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """result.json's `tests`, reduced to the documented fields with the right types."""
    if not isinstance(raw, dict):
        return None
    command, exit_code = raw.get("command"), raw.get("exit_code")
    passed, duration = raw.get("passed"), raw.get("duration_s")
    if not isinstance(command, str) or not isinstance(passed, bool):
        return None
    return {
        "command": redact(command)[:1000],
        "exit_code": exit_code
        if isinstance(exit_code, int) and not isinstance(exit_code, bool)
        else None,
        "passed": passed,
        "duration_s": float(duration)
        if isinstance(duration, int | float) and not isinstance(duration, bool)
        else None,
    }


# Identifies this worker process tree: generated once when the module is imported by
# the Celery main process, and inherited by its forked children. A restarted worker gets
# a new one, so sandboxes owned by the previous boot are recognisably orphaned.
WORKER_BOOT_ID = uuid.uuid4().hex[:12]


def sandbox_owner() -> str:
    return f"{WORKER_BOOT_ID}:{os.getpid()}"


def owner_is_alive(owner: str) -> bool:
    """True while the process that started a sandbox (this worker boot) is still running."""
    boot, _, pid = owner.partition(":")
    if boot != WORKER_BOOT_ID or not pid.isdigit():
        return False
    if os.name != "posix":
        # Workers run in Linux containers; elsewhere (tests on Windows) signal 0 means
        # Ctrl+C, so only this very process counts as alive.
        return int(pid) == os.getpid()
    try:
        os.kill(int(pid), 0)  # signal 0: existence check only
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass
class WorkerContext:
    sessions: async_sessionmaker[AsyncSession]
    gh: GitHubClient
    publisher: EventPublisher
    sandbox: Sandbox
    pusher: BranchPusher
    settings: Settings
    # Queues follow-up runs (a review that arrived mid-run); None in tests that don't care.
    enqueue: RunEnqueuer | None = None
    # Whether the worker process named in a sandbox's owner label is still watching it.
    owner_alive: Callable[[str], bool] = owner_is_alive
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
    config: NextixConfig | None = None,
    limits: RunLimits | None = None,
    review_comments: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """The complete environment contract (docs/phase3.md and phase4.md). Nothing else is passed.

    The task is the run's snapshot (what the trusted person approved), not the live issue.
    """
    title = run.task_title if run.task_title is not None else ticket.title
    body = run.task_body if run.task_body is not None else (ticket.body or "")
    if len(body) > MAX_ISSUE_BODY_CHARS:
        body = body[:MAX_ISSUE_BODY_CHARS] + "\n\n… [the rest of the issue was cut: too long]"
    if clarification:
        clarification = clarification[:MAX_CLARIFICATION_CHARS]
        body = f"{body}\n\n{clarification}" if body else clarification
    config = config or NextixConfig()
    limits = limits or RunLimits.resolve(settings, config)
    task = {
        "title": title,
        "body": body,
        "extra_instructions": config.agent.extra_instructions or "",
        "review_comments": list(review_comments or []),
    }
    task_json = fit_task(task)
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
        "NEXTIX_TASK_JSON": task_json,
        "NEXTIX_MODEL": run.model or settings.model_for(list(ticket.labels)),
        "NEXTIX_MAX_TURNS": str(limits.max_turns),
        "NEXTIX_TIMEOUT_MIN": str(limits.timeout_min),
        "NEXTIX_MAX_COST_USD": str(limits.max_cost_usd),
        "NEXTIX_ALLOWED_TOOLS": limits.allowed_tools,
        "NEXTIX_CONFIG_JSON": json.dumps(config.sandbox_json(), ensure_ascii=False),
        "GITHUB_TOKEN": clone_token,
        **claude_credential_env(settings),
    }


# One environment variable must stay under Linux's 128 KiB; leave room for the rest.
MAX_TASK_BYTES = 100_000
MAX_REVIEW_COMMENTS = 50
MAX_REVIEW_COMMENT_CHARS = 4000


def fit_task(task: dict[str, Any]) -> str:
    """NEXTIX_TASK_JSON, trimmed to MAX_TASK_BYTES.

    Review comments are what a feedback run is about, so a long issue body is shortened
    (to at most a third of the budget) before any comment is dropped; after that the last
    comments go, and only then the rest of the body.
    """
    task = {**task, "review_comments": list(task.get("review_comments") or [])}

    def size() -> int:
        return len(json.dumps(task, ensure_ascii=False).encode())

    def shorten_body() -> None:
        body = task["body"].removesuffix(_CUT)
        task["body"] = body[: len(body) * 3 // 4] + _CUT

    while size() > MAX_TASK_BYTES and len(task["body"].encode()) > MAX_TASK_BYTES // 3:
        shorten_body()
    while size() > MAX_TASK_BYTES and task["review_comments"]:
        task["review_comments"].pop()
    while size() > MAX_TASK_BYTES and task["body"]:
        shorten_body()
    return json.dumps(task, ensure_ascii=False)


_CUT = "\n\n… [cut: too long]"


async def review_comments_for(
    run: Run, ticket: Ticket, repo: Repo, ctx: WorkerContext
) -> list[dict[str, Any]]:
    """The review a review_feedback run answers: its body and inline comments."""
    if run.review_id is None or ticket.pr_number is None:
        return []
    comments: list[dict[str, Any]] = []
    try:
        review = await ctx.gh.get_review(
            repo.installation_id, repo.owner, repo.name, ticket.pr_number, run.review_id
        )
        inline = await ctx.gh.list_review_comments(
            repo.installation_id, repo.owner, repo.name, ticket.pr_number, run.review_id
        )
    except Exception:
        log.exception("could not read review %s for run %s", run.review_id, run.id)
        return []
    author = review.user.login if review.user else None
    if (review.body or "").strip():
        comments.append(
            {"author": author, "body": redact(review.body or "")[:MAX_REVIEW_COMMENT_CHARS]}
        )
    for item in inline[:MAX_REVIEW_COMMENTS]:
        if not (item.body or "").strip():
            continue
        comments.append(
            {
                "path": item.path,
                "line": item.line or item.original_line,
                "author": item.user.login if item.user else author,
                "body": redact(item.body or "")[:MAX_REVIEW_COMMENT_CHARS],
            }
        )
    return comments


def tests_line(tests: dict[str, Any] | None) -> str | None:
    if not tests:
        return None
    took = f", {tests['duration_s']:.0f} s" if tests.get("duration_s") is not None else ""
    if tests["passed"]:
        return f"✅ Tests passed: `{tests['command']}`{took}"
    code = f"exit {tests['exit_code']}" if tests.get("exit_code") is not None else "failed"
    return f"❌ Tests failed: `{tests['command']}` ({code}{took})"


def screenshots_table(shots: list[Artifact]) -> str | None:
    diffs = [a for a in shots if a.kind == "screenshot_diff"]
    if not diffs:
        return None
    rows = []
    for diff in sorted(diffs, key=lambda a: a.label or ""):
        pct = (diff.meta or {}).get("diff_pct")
        change = "no visible change" if pct == 0 else f"{pct:.2f}% of pixels" if pct else "?"
        rows.append(f"| `{diff.label}` | {change} |")
    return "| Route | Changed |\n|---|---|\n" + "\n".join(rows)


def pr_body(
    *,
    settings: Settings,
    run: Run,
    ticket: Ticket,
    result: RunResult,
    shots: list[Artifact] | None = None,
) -> str:
    link = f"{settings.nextix_public_url.rstrip('/')}/tickets/{ticket.id}"
    duration = ""
    if run.started_at:
        seconds = int((datetime.now(UTC) - run.started_at).total_seconds())
        duration = f" · {seconds // 60}m {seconds % 60:02d}s"
    summary = redact(result.summary.strip()) or "_The agent did not leave a summary._"
    tokens = result.input_tokens + result.output_tokens
    review = [
        line for line in (tests_line(run.tests), screenshots_table(shots or [])) if line is not None
    ]
    checks = ("\n\n".join(review) + "\n\n") if review else ""
    return (
        f"{summary}\n\n"
        f"Closes #{ticket.issue_number}\n\n"
        f"{checks}"
        f"[Agent transcript, screenshots and tests on the nexTix board]({link})\n\n"
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


class RepoBusy(Exception):
    """The run's repository already has NEXTIX_MAX_RUNS_PER_REPO active runs; try later."""


async def _claim(session: AsyncSession, run_id: uuid.UUID, ctx: WorkerContext) -> Run | None:
    """queued → claimed, atomically. None if someone else has it or it's no longer queued.

    Raises RepoBusy (the run stays queued) when its repository is at its run limit.
    """
    run = await session.scalar(
        select(Run).where(Run.id == run_id).with_for_update(skip_locked=True)
    )
    if run is None or run.status != RunStatus.QUEUED:
        await session.rollback()
        return None
    repo_id = await session.scalar(select(Ticket.repo_id).where(Ticket.id == run.ticket_id))
    # Serialise claims per repository, so two workers can't both take its last slot.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"nextix-repo:{repo_id}"}
    )
    active = await session.scalar(
        select(func.count())
        .select_from(Run)
        .join(Ticket, Ticket.id == Run.ticket_id)
        .where(
            Ticket.repo_id == repo_id,
            Run.status.in_((RunStatus.CLAIMED, RunStatus.RUNNING)),
        )
    )
    if (active or 0) >= ctx.settings.nextix_max_runs_per_repo:
        await session.rollback()
        raise RepoBusy(str(repo_id))
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


async def _wait(
    session: AsyncSession, run: Run, container_id: str, ctx: WorkerContext, timeout_min: int
) -> str:
    """Wait for the sandbox. Returns 'exited', 'timeout', or 'stopped' (cancel/reaper)."""
    deadline = time.monotonic() + timeout_min * 60 + ctx.grace_s
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


# Tar overhead on top of the artifact caps.
ARTIFACT_ARCHIVE_SLACK = 4 * 1024 * 1024


async def _read_artifacts(ctx: WorkerContext, container_id: str) -> dict[str, bytes] | None:
    try:
        return await asyncio.to_thread(
            ctx.sandbox.read_tree,
            container_id,
            ARTIFACTS_SANDBOX_DIR,
            max_bytes=MAX_ARTIFACT_BYTES + ARTIFACT_ARCHIVE_SLACK,
        )
    except Exception:
        log.exception("could not copy the artifacts out of %s", container_id[:12])
        return None


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
    """Remove sandboxes nobody is watching any more, e.g. after the runner restarted.

    Each sandbox is labelled with the worker process that started it. A sandbox whose
    owner is gone (a previous worker boot, or a process that died) would never have its
    result collected: it is removed, and its run, if still active, fails with
    `worker_restarted` so it can be retried. Sandboxes of live sibling processes (with
    RUNNER_CONCURRENCY above 1) are left alone.
    """
    labelled = {
        run_id: owner
        for run_id, owner in (await asyncio.to_thread(ctx.sandbox.labelled_runs)).items()
        if not ctx.owner_alive(owner)
    }
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
    return set(labelled)


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

        try:
            repo_config = await load_repo_config(session, ctx.gh, repo)
        except ConfigError as exc:
            await _finish(
                session,
                run,
                RunStatus.FAILED,
                ctx,
                exit_reason="bad_config",
                comment=(
                    f"❌ Failed: `{CONFIG_PATH}` on `{repo.default_branch}` can't be used:"
                    f"\n\n```\n{redact(str(exc))}\n```\n\nFix it on `{repo.default_branch}`, "
                    "then retry."
                ),
            )
            return
        limits = RunLimits.resolve(ctx.settings, repo_config.config)
        run.model = ctx.settings.model_for(list(ticket.labels))
        await session.commit()

        container_id: str | None = None
        keep_alive: asyncio.Task[None] | None = None
        files: dict[str, bytes] | None = None
        outcome = "exited"
        result: RunResult | None = None
        bundle: bytes | None = None
        try:
            clone_token = await ctx.gh.auth.scoped_token(
                repo.installation_id, repository=repo.name, permissions={"contents": "read"}
            )
            clarification = await clarification_for(session, run, ticket, repo, ctx)
            review_comments = await review_comments_for(run, ticket, repo, ctx)
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
                    config=repo_config.config,
                    limits=limits,
                    review_comments=review_comments,
                ),
                owner=sandbox_owner(),
                network=ctx.settings.agent_network or None,
                mem_limit=ctx.settings.agent_mem_limit,
                nano_cpus=int(ctx.settings.agent_cpus * 1_000_000_000),
                pids_limit=ctx.settings.agent_pids_limit,
            )
            container_id = await asyncio.to_thread(ctx.sandbox.start, spec)
            run.container_id = container_id
            await session.commit()
            outcome = await _wait(session, run, container_id, ctx, limits.timeout_min)
            keep_alive = asyncio.create_task(_keep_alive(ctx, run.id))
            result = _parse_result(
                await asyncio.to_thread(ctx.sandbox.read_file, container_id, RESULT_PATH)
            )
            if result and result.commits > 0:
                bundle = await asyncio.to_thread(ctx.sandbox.read_file, container_id, BUNDLE_PATH)
            files = await _read_artifacts(ctx, container_id)
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
            collected = await collect_artifacts(session, run, files, ctx.settings.artifact_dir)
            if collected.errors:
                run.review_errors = collected.errors
                await session.commit()
            await _conclude(
                session,
                run,
                ticket,
                repo,
                ctx,
                outcome=outcome,
                result=result,
                bundle=bundle,
                limits=limits,
                shots=collected.artifacts,
            )
        finally:
            if keep_alive:
                keep_alive.cancel()
        await _start_pending_review(session, run.ticket_id, ctx)


async def _start_pending_review(
    session: AsyncSession, ticket_id: uuid.UUID, ctx: WorkerContext
) -> None:
    if ctx.enqueue is None:
        return
    try:
        await start_pending_review(
            session, ticket_id, gh=ctx.gh, publisher=ctx.publisher, enqueue=ctx.enqueue
        )
    except Exception:
        log.exception("could not queue the pending review of ticket %s", ticket_id)


async def _record_usage(session: AsyncSession, run: Run, result: RunResult | None) -> None:
    if result is None:
        return
    await session.refresh(run)
    run.input_tokens = max(run.input_tokens, result.input_tokens)
    run.output_tokens = max(run.output_tokens, result.output_tokens)
    run.cost_usd = max(run.cost_usd, Decimal(str(round(result.cost_usd, 4))))
    run.summary = redact(result.summary)[:4000] or None
    run.tests = clean_tests(result.tests)
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
    limits: RunLimits,
    shots: list[Artifact],
) -> None:
    await session.refresh(run)
    if run.status in TERMINAL_STATUSES:
        return  # the owner cancelled, or the reaper already failed it and commented
    if result is None:
        if outcome == "timeout":
            minutes = limits.timeout_min
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
        pr, created = await _open_or_update_pr(run, ticket, repo, ctx, result, shots)
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
    ticket.pr_head_sha = pr["head_sha"] or ticket.pr_head_sha
    ticket.updated_at = datetime.now(UTC)
    await session.commit()
    verb = "Opened" if created else "Updated"
    reviewer = (run.review_meta or {}).get("author") if run.trigger == "review_feedback" else None
    await _finish(
        session,
        run,
        RunStatus.SUCCEEDED,
        ctx,
        comment=(
            (
                f"✅ Updated PR #{pr['number']} to address @{reviewer}'s review "
                if reviewer
                else f"✅ {verb} PR #{pr['number']} from `{run.branch}` "
            )
            + f"(${result.cost_usd:.2f}, {result.num_turns} turns)."
            + (f"\n\n{line}" if (line := tests_line(run.tests)) else "")
        ),
    )


async def _open_or_update_pr(
    run: Run,
    ticket: Ticket,
    repo: Repo,
    ctx: WorkerContext,
    result: RunResult,
    shots: list[Artifact],
) -> tuple[dict[str, Any], bool]:
    title = f"[nextix #{ticket.issue_number}] {ticket.title}"
    body = pr_body(settings=ctx.settings, run=run, ticket=ticket, result=result, shots=shots)
    existing = await ctx.gh.find_open_pull(
        repo.installation_id, repo.owner, repo.name, branch=run.branch or ""
    )
    if existing:
        pr = await ctx.gh.update_pull(
            repo.installation_id, repo.owner, repo.name, existing.number, title=title, body=body
        )
        return {"number": pr.number, "head_sha": pr.head.sha}, False
    pr = await ctx.gh.create_pull(
        repo.installation_id,
        repo.owner,
        repo.name,
        title=title,
        body=body,
        head=run.branch or "",
        base=repo.default_branch,
    )
    return {"number": pr.number, "head_sha": pr.head.sha}, True


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
