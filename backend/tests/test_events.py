"""Redis fan-out: what a publisher sends, a subscriber receives."""

import asyncio
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as aioredis

from nextix.events.stream import RedisPublisher, subscription


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[aioredis.Redis]:
    client = aioredis.Redis.from_url(redis_url)
    yield client
    await client.aclose()


async def test_published_events_reach_subscribers(redis_client: aioredis.Redis) -> None:
    async with subscription(redis_client, "test:board") as messages:
        # The subscription is live on entry, so nothing published now is missed.
        await RedisPublisher(redis_client).publish("test:board", "ticket.updated", {"id": "t1"})
        await redis_client.publish("test:board", "not json")  # malformed: skipped
        await RedisPublisher(redis_client).publish("test:board", "ticket.removed", {"id": "t2"})

        received = []
        async with asyncio.timeout(5):
            async for msg in messages:
                received.append(msg)
                if len(received) == 2:
                    break

    assert received == [
        {"event": "ticket.updated", "data": {"id": "t1"}},
        {"event": "ticket.removed", "data": {"id": "t2"}},
    ]
