"""Shared FastAPI dependencies backed by clients created in the app lifespan."""

import uuid
from typing import cast

import redis.asyncio as aioredis
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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


def celery_enqueue(run_id: uuid.UUID) -> None:
    """Hand a queued run to the worker on the dedicated, one-at-a-time `runs` queue."""
    from nextix.celery_app import celery_app

    celery_app.send_task("nextix.execute_run", args=[str(run_id)], queue="runs")


def get_enqueuer() -> RunEnqueuer:
    return celery_enqueue
