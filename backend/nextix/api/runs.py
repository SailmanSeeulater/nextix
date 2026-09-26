"""Ticket detail, run history and live run streams, retry and cancel (docs/phase3.md)."""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette import EventSourceResponse

from nextix.api.auth import require_user
from nextix.api.deps import get_enqueuer, get_github, get_publisher, get_redis, get_sessionmaker
from nextix.db.models import Run, RunEvent, Ticket
from nextix.db.session import get_db
from nextix.events.stream import EventPublisher, subscription
from nextix.github.client import GitHubClient
from nextix.runs.events import event_json, run_channel
from nextix.runs.lifecycle import (
    ActiveRunExists,
    RunEnqueuer,
    enqueue_run,
    record_transition,
    run_detail,
)
from nextix.runs.state import ACTIVE_STATUSES, RunStatus
from nextix.tickets.service import get_card

router = APIRouter(tags=["runs"], dependencies=[Depends(require_user)])

PING_INTERVAL_S = 15


@router.get("/tickets/{ticket_id}")
async def ticket_detail(
    ticket_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    found = await get_card(session, ticket_id)
    ticket = await session.get(Ticket, ticket_id)
    if found is None or ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such ticket")
    card, _ = found
    runs = await session.scalars(
        select(Run).where(Run.ticket_id == ticket_id).order_by(Run.attempt.desc())
    )
    return {
        **card.model_dump(mode="json"),
        "body": ticket.body,
        "runs": [run_detail(r) for r in runs],
    }


@router.get("/runs/{run_id}/events")
async def list_run_events(
    run_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    if await session.get(Run, run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")
    rows = list(
        await session.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.id > after)
            .order_by(RunEvent.id)
            .limit(limit + 1)
        )
    )
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "events": [event_json(r) for r in rows],
        "next_after": rows[-1].id if more and rows else None,
    }


@router.get("/runs/{run_id}/stream")
async def run_stream(
    run_id: uuid.UUID,
    client: Annotated[aioredis.Redis, Depends(get_redis)],
    sessions: Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)],
) -> EventSourceResponse:
    async with sessions() as session:
        if await session.get(Run, run_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")

    async def events() -> AsyncIterator[dict[str, Any]]:
        # Subscribe before replaying, so nothing published during the replay is lost;
        # the client de-duplicates by event id.
        async with subscription(client, run_channel(run_id)) as messages:
            async with sessions() as session:
                rows = await session.scalars(
                    select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.id)
                )
                for row in rows:
                    yield {
                        "event": row.kind,
                        "id": str(row.id),
                        "data": json.dumps(event_json(row)),
                    }
            yield {"event": "ready", "data": "{}"}
            async for msg in messages:
                data = msg.get("data")
                out: dict[str, Any] = {
                    "event": msg.get("event", "message"),
                    "data": json.dumps(data),
                }
                if isinstance(data, dict) and isinstance(data.get("id"), int):
                    out["id"] = str(data["id"])
                yield out

    return EventSourceResponse(events(), ping=PING_INTERVAL_S)


@router.post("/tickets/{ticket_id}/runs", status_code=status.HTTP_201_CREATED)
async def retry_ticket(
    ticket_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    gh: Annotated[GitHubClient, Depends(get_github)],
    publisher: Annotated[EventPublisher, Depends(get_publisher)],
    enqueue: Annotated[RunEnqueuer, Depends(get_enqueuer)],
) -> dict[str, Any]:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such ticket")
    if ticket.issue_state != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "the issue is closed; reopen it first")
    try:
        run = await enqueue_run(
            session, ticket, trigger="retry", gh=gh, publisher=publisher, enqueue=enqueue
        )
    except ActiveRunExists as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"this ticket already has a {exc.run.status} run"
        ) from exc
    except Exception as exc:  # the queue (Redis) is unreachable; enqueue_run cancelled it
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "could not reach the job queue; try again"
        ) from exc
    return run_detail(run)


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(
    run_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    gh: Annotated[GitHubClient, Depends(get_github)],
    publisher: Annotated[EventPublisher, Depends(get_publisher)],
) -> dict[str, Any]:
    """Mark the run cancelled. A running sandbox learns of it on its next callback (410),
    and the worker, which polls the run while waiting, stops the container."""
    run = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")
    if run.status not in ACTIVE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"run is already {run.status}")
    await record_transition(
        session, run, RunStatus.CANCELLED, gh=gh, publisher=publisher, exit_reason="cancelled"
    )
    return run_detail(run)
