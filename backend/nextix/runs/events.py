"""Storing run events and fanning them out to live run streams.

Everything that belongs in a run's transcript goes through `store_events`: agent messages
and tool calls from the sandbox, and state changes from the worker. Each stored event is
published to the run's Redis channel with its database id, so a stream that replays stored
events and then tails the channel can de-duplicate by id.
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nextix.db.models import RunEvent
from nextix.events.stream import EventPublisher
from nextix.redact import redact_obj

log = logging.getLogger(__name__)

TOOL_RESULT_LIMIT = 20_000
STORED_KINDS = frozenset({"state", "log", "message", "tool_use", "tool_result", "usage", "error"})


def run_channel(run_id: uuid.UUID) -> str:
    return f"nextix:run:{run_id}"


def event_json(event: RunEvent) -> dict[str, Any]:
    """The RunEvent shape from docs/phase3.md."""
    ts: datetime = event.ts
    return {"id": event.id, "ts": ts.isoformat(), "kind": event.kind, "payload": event.payload}


def clean_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Redact credentials and cap oversized tool output before anything is stored."""
    cleaned: dict[str, Any] = redact_obj(payload)
    if kind == "tool_result":
        content = cleaned.get("content")
        if isinstance(content, str) and len(content) > TOOL_RESULT_LIMIT:
            cleaned["content"] = content[:TOOL_RESULT_LIMIT] + "\n… [truncated]"
    return cleaned


async def store_events(
    session: AsyncSession,
    publisher: EventPublisher,
    run_id: uuid.UUID,
    events: list[tuple[str, dict[str, Any]]],
) -> list[RunEvent]:
    """Insert events (kind, payload), commit, then publish each with its id.

    The commit also persists any pending changes on the session (e.g. a status change),
    so callers can pair an update with its event atomically.
    """
    rows = [
        RunEvent(run_id=run_id, kind=kind, payload=clean_payload(kind, payload))
        for kind, payload in events
        if kind in STORED_KINDS
    ]
    session.add_all(rows)
    await session.commit()
    for row in rows:
        await session.refresh(row)
        try:
            await publisher.publish(run_channel(run_id), row.kind, event_json(row))
        except Exception:
            log.exception("could not publish run event %s", row.id)
    return rows
