"""Shared FastAPI dependencies backed by clients created in the app lifespan."""

from typing import cast

import redis.asyncio as aioredis
from fastapi import Request

from nextix.events.stream import EventPublisher, RedisPublisher
from nextix.github.client import GitHubClient


def get_github(request: Request) -> GitHubClient:
    return cast(GitHubClient, request.app.state.github)


def get_redis(request: Request) -> aioredis.Redis:
    return cast(aioredis.Redis, request.app.state.redis)


def get_publisher(request: Request) -> EventPublisher:
    return RedisPublisher(get_redis(request))
