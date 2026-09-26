"""The reaper (Celery beat, every 30 s): fail runs whose agent went silent.

A run that is `claimed`/`running` but hasn't sent a heartbeat for 90 s has lost its sandbox
(killed, crashed, or wedged). It becomes `failed` with `exit_reason = "heartbeat_lost"`, its
container is killed by label, and the issue gets a comment. Runs stuck in `queued` are left
alone: the board shows "No worker available" from `queued_at`.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.db.models import Run
from nextix.events.stream import EventPublisher
from nextix.github.client import GitHubClient
from nextix.runs.lifecycle import record_transition
from nextix.runs.sandbox import Sandbox
from nextix.runs.state import RunStatus

log = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT = timedelta(seconds=90)
SILENT = (RunStatus.CLAIMED, RunStatus.RUNNING)


async def reap(
    session: AsyncSession,
    *,
    gh: GitHubClient,
    publisher: EventPublisher,
    sandbox: Sandbox | None,
    now: datetime,
) -> list[uuid.UUID]:
    """Fail every silent run; return their ids."""
    cutoff = now - HEARTBEAT_TIMEOUT
    candidates = list(
        await session.scalars(
            select(Run.id).where(
                Run.status.in_(SILENT),
                func.coalesce(Run.last_heartbeat, Run.started_at, Run.queued_at) < cutoff,
            )
        )
    )
    await session.commit()

    reaped: list[uuid.UUID] = []
    for run_id in candidates:
        # Re-check under a row lock: a heartbeat may have landed since the scan.
        run = await session.scalar(
            select(Run)
            .where(Run.id == run_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if run is None or run.status not in SILENT:
            await session.rollback()
            continue
        heard = run.last_heartbeat or run.started_at or run.queued_at
        if heard >= cutoff:
            await session.rollback()
            continue
        await record_transition(
            session,
            run,
            RunStatus.FAILED,
            gh=gh,
            publisher=publisher,
            exit_reason="heartbeat_lost",
            now=now,
        )
        reaped.append(run.id)
        if sandbox is not None:
            try:
                await asyncio.to_thread(sandbox.kill_run, run.id)
            except Exception:
                log.exception("could not remove the container for run %s", run.id)
        log.warning("reaped run %s (no heartbeat since %s)", run.id, heard)
    return reaped
