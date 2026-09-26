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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from nextix.celery_app import celery_app
from nextix.config import get_settings
from nextix.events.stream import RedisPublisher
from nextix.github.app_auth import GitHubAppAuth
from nextix.github.client import GitHubClient
from nextix.runs.executor import WorkerContext, execute_run
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
        return await reap(
            session,
            gh=ctx.gh,
            publisher=ctx.publisher,
            sandbox=sandbox,
            now=datetime.now(UTC),
        )


@celery_app.task(name="nextix.execute_run")
def execute_run_task(run_id: str) -> None:
    asyncio.run(_execute(uuid.UUID(run_id)))


@celery_app.task(name="nextix.reap_runs", ignore_result=True)
def reap_runs_task() -> list[str]:
    return [str(r) for r in asyncio.run(_reap(_reaper_sandbox()))]
