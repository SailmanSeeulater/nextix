"""Celery entry points for runs.

`nextix.execute_run` goes to the `runs` queue (one worker, concurrency 1, with the Docker
socket); `nextix.reap_runs` runs on the default queue every 30 s from beat. Each task builds
its own clients inside `asyncio.run`: pooled connections can't cross event loops, so the
database uses a NullPool engine per task.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import redis.asyncio as aioredis
from celery import Task
from celery.signals import worker_ready
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from nextix.api.deps import celery_enqueue
from nextix.celery_app import celery_app
from nextix.config import get_settings
from nextix.events.stream import RedisPublisher
from nextix.github.app_auth import GitHubAppAuth
from nextix.github.client import GitHubClient
from nextix.runs.executor import RepoBusy, WorkerContext, execute_run, sweep_orphans
from nextix.runs.lifecycle import start_pending_reviews
from nextix.runs.push import GitBundlePusher
from nextix.runs.reaper import reap
from nextix.runs.sandbox import DockerSandbox, Sandbox

log = logging.getLogger(__name__)

GITHUB_HTTP_TIMEOUT_S = 20.0
DOCKER_SOCKET = "/var/run/docker.sock"


@asynccontextmanager
async def worker_context(*, sandbox: Sandbox | None) -> AsyncIterator[WorkerContext]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    http = httpx.AsyncClient(timeout=GITHUB_HTTP_TIMEOUT_S)
    redis = aioredis.Redis.from_url(settings.redis_url)
    try:
        yield WorkerContext(
            sessions=async_sessionmaker(engine, expire_on_commit=False),
            gh=GitHubClient(
                GitHubAppAuth.from_settings(settings, http), http, settings.github_api_url
            ),
            publisher=RedisPublisher(redis),
            sandbox=sandbox,  # type: ignore[arg-type]  # None only for the reaper
            pusher=GitBundlePusher(),
            settings=settings,
            enqueue=celery_enqueue,
        )
    finally:
        await http.aclose()
        await redis.aclose()
        await engine.dispose()


async def _execute(run_id: uuid.UUID) -> None:
    async with worker_context(sandbox=DockerSandbox()) as ctx:
        await execute_run(run_id, ctx)


def _reaper_sandbox() -> Sandbox | None:
    """The ordinary worker has no Docker socket by design: the runner removes a reaped
    run's container (it sees the terminal status). Kill it here too when we can."""
    if not Path(DOCKER_SOCKET).exists():
        return None
    try:
        return DockerSandbox()
    except Exception:
        log.exception("the Docker socket is present but unusable")
        return None


async def _reap(sandbox: Sandbox | None) -> list[uuid.UUID]:
    async with worker_context(sandbox=sandbox) as ctx, ctx.sessions() as session:
        reaped = await reap(
            session,
            gh=ctx.gh,
            publisher=ctx.publisher,
            sandbox=sandbox,
            now=datetime.now(UTC),
        )
        # Reviews that arrived during a run which then ended off the normal path.
        await start_pending_reviews(
            session, gh=ctx.gh, publisher=ctx.publisher, enqueue=celery_enqueue
        )
        return reaped


async def _sweep_on_start(sandbox: Sandbox) -> None:
    async with worker_context(sandbox=sandbox) as ctx, ctx.sessions() as session:
        removed = await sweep_orphans(session, ctx)
    if removed:
        log.warning("removed %d sandbox(es) left from before this worker started", len(removed))


@worker_ready.connect
def sweep_sandboxes_on_start(**_: object) -> None:
    """A restarted runner fails the run it was supervising right away, instead of leaving
    its sandbox running unwatched until the next run starts. Only the runner has the
    Docker socket, so the ordinary worker skips this."""
    sandbox = _reaper_sandbox()
    if sandbox is None:
        return
    try:
        asyncio.run(_sweep_on_start(sandbox))
    except Exception:
        log.exception("could not sweep leftover sandboxes on start")


# How long a run whose repository is busy waits before the worker looks again.
REPO_BUSY_RETRY_S = 20


@celery_app.task(name="nextix.execute_run", bind=True, max_retries=None)
def execute_run_task(self: Task, run_id: str) -> None:  # type: ignore[type-arg]
    try:
        asyncio.run(_execute(uuid.UUID(run_id)))
    except RepoBusy:
        # The run stays queued ("Waiting for a worker" on the board) until a slot frees.
        raise self.retry(countdown=REPO_BUSY_RETRY_S) from None


@celery_app.task(name="nextix.reap_runs", ignore_result=True)
def reap_runs_task() -> list[str]:
    return [str(r) for r in asyncio.run(_reap(_reaper_sandbox()))]
