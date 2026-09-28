"""GET /api/stream: board events as Server-Sent Events.

Clients should (re)load ``GET /api/tickets`` when the stream opens, then apply
events. A ``ready`` event is sent once the Redis subscription is live, so the
client knows it can't miss events published after that point.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sse_starlette import EventSourceResponse

from nextix.api.auth import require_user
from nextix.api.deps import get_redis
from nextix.events.stream import BOARD_CHANNEL, subscription

log = logging.getLogger(__name__)

router = APIRouter(tags=["stream"], dependencies=[Depends(require_user)])

PING_INTERVAL_S = 15


async def board_events(client: aioredis.Redis) -> AsyncIterator[dict[str, Any]]:
    """The SSE events: ``ready``, then every board message as it is published."""
    async with subscription(client, BOARD_CHANNEL) as messages:
        yield {"event": "ready", "data": "{}"}
        async for msg in messages:
            # One malformed message (valid JSON, but not from our publisher) mustn't end
            # every open board's stream.
            if not isinstance(msg, dict) or "event" not in msg or "data" not in msg:
                log.warning("skipping a malformed board message: %.200r", msg)
                continue
            yield {"event": msg["event"], "data": json.dumps(msg["data"])}


@router.get("/stream")
async def board_stream(
    client: Annotated[aioredis.Redis, Depends(get_redis)],
) -> EventSourceResponse:
    return EventSourceResponse(board_events(client), ping=PING_INTERVAL_S)
