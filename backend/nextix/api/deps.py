"""Shared FastAPI dependencies backed by clients created in the app lifespan."""

import uuid
from typing import cast

import redis.asyncio as aioredis
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.config import get_settings
from nextix.db.session import get_async_sessionmaker
from nextix.events.stream import EventPublisher, RedisPublisher
from nextix.github.client import GitHubClient
from nextix.runs.lifecycle import RunEnqueuer
from nextix.tickets.triage import Triager


def get_github(request: Request) -> GitHubClient:
    return cast(GitHubClient, request.app.state.github)


def get_redis(request: Request) -> aioredis.Redis:
    return cast(aioredis.Redis, request.app.state.redis)


def get_publisher(request: Request) -> EventPublisher:
    return RedisPublisher(get_redis(request))


def get_triager(request: Request) -> Triager | None:
    """None when no Anthropic key is configured; callers then refuse triaged requests."""
    return cast(Triager | None, getattr(request.app.state, "triager", None))


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """For work that outlives a request's session, such as a streaming response."""
    return get_async_sessionmaker()


# After the agent's own timeout and the worker's 2-minute grace: time to read the bundle,
# push, and open the PR, then a margin before Celery kills the task outright.
PUBLISH_BUDGET_S = 15 * 60
HARD_LIMIT_MARGIN_S = 5 * 60


def run_time_limits(timeout_min: int) -> tuple[int, int]:
    """(soft, hard) Celery time limits for one run, derived from the agent timeout."""
    soft = timeout_min * 60 + 120 + PUBLISH_BUDGET_S
    return soft, soft + HARD_LIMIT_MARGIN_S


def celery_enqueue(run_id: uuid.UUID) -> None:
    """Hand a queued run to the worker on the dedicated, one-at-a-time `runs` queue."""
    from nextix.celery_app import celery_app

    # A repo's .nextix.yml may raise the timeout (up to MAX_TIMEOUT_MIN), and it is read
    # only once the run starts, so the limits cover the largest timeout it could set.
    from nextix.runs.config import MAX_TIMEOUT_MIN

    soft, hard = run_time_limits(max(get_settings().agent_default_timeout_min, MAX_TIMEOUT_MIN))
    celery_app.send_task(
        "nextix.execute_run",
        args=[str(run_id)],
        queue="runs",
        soft_time_limit=soft,
        time_limit=hard,
    )


def get_enqueuer() -> RunEnqueuer:
    return celery_enqueue
