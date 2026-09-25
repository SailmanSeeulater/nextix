"""GET /api/stream: board events as Server-Sent Events.

Clients should (re)load ``GET /api/tickets`` when the stream opens, then apply
events. A ``ready`` event is sent once the Redis subscription is live, so the
client knows it can't miss events published after that point.
"""

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sse_starlette import EventSourceResponse

from nextix.api.auth import require_user
from nextix.api.deps import get_redis
from nextix.events.stream import BOARD_CHANNEL, subscription

router = APIRouter(tags=["stream"], dependencies=[Depends(require_user)])

PING_INTERVAL_S = 15


@router.get("/stream")
async def board_stream(
    client: Annotated[aioredis.Redis, Depends(get_redis)],
) -> EventSourceResponse:
    async def events() -> AsyncIterator[dict[str, Any]]:
        async with subscription(client, BOARD_CHANNEL) as messages:
            yield {"event": "ready", "data": "{}"}
            async for msg in messages:
                yield {"event": msg["event"], "data": json.dumps(msg["data"])}

    return EventSourceResponse(events(), ping=PING_INTERVAL_S)
