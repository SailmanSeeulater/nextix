"""Live events: publish to Redis pub/sub, fan out to SSE subscribers.

Board-level events go on one channel. Each message is JSON:
``{"event": "ticket.updated", "data": {...}}``.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.tickets.service import get_card

log = logging.getLogger(__name__)

BOARD_CHANNEL = "nextix:board"


class EventPublisher(Protocol):
    async def publish(self, channel: str, event: str, data: dict[str, Any]) -> None: ...


class RedisPublisher:
    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client

    async def publish(self, channel: str, event: str, data: dict[str, Any]) -> None:
        await self._client.publish(channel, json.dumps({"event": event, "data": data}))


async def publish_ticket_changes(
    session: AsyncSession, publisher: EventPublisher, ticket_ids: set[uuid.UUID]
) -> None:
    """Publish ``ticket.updated`` (or ``ticket.removed``) for each ticket.

    Call after commit. Failures are logged, never raised: the DB is already
    correct and clients resync on reconnect, so a lost event is not fatal.
    """
    for ticket_id in ticket_ids:
        try:
            found = await get_card(session, ticket_id)
            if found is None:
                continue
            card, on_board = found
            if on_board:
                await publisher.publish(
                    BOARD_CHANNEL, "ticket.updated", card.model_dump(mode="json")
                )
            else:
                await publisher.publish(BOARD_CHANNEL, "ticket.removed", {"id": str(ticket_id)})
        except Exception:
            log.exception("failed to publish board event for ticket %s", ticket_id)


@asynccontextmanager
async def subscription(
    client: aioredis.Redis, channel: str
) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
    """Subscribe to ``channel``; the subscription is live when the block is entered.

    Yields an iterator of decoded ``{"event", "data"}`` messages.
    """
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)

    async def messages() -> AsyncIterator[dict[str, Any]]:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                decoded: dict[str, Any] = json.loads(message["data"])
            except (TypeError, ValueError):
                log.warning("dropping malformed message on %s", channel)
                continue
            yield decoded

    try:
        yield messages()
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()  # type: ignore[no-untyped-call]
