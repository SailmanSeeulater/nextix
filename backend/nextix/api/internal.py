"""POST /api/internal/runs/{id}/events: the sandbox's only way to talk to nexTix.

Authenticated by an HMAC over the raw body with the run's own secret (not the API token),
so a sandbox can only ever report on its own run, and only while that run is active.
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.api.deps import get_github, get_publisher
from nextix.db.models import Run
from nextix.db.session import get_db
from nextix.events.stream import EventPublisher, publish_ticket_changes
from nextix.github.client import GitHubClient
from nextix.runs.events import store_events
from nextix.runs.lifecycle import publish_run, record_transition
from nextix.runs.state import TERMINAL_STATUSES, RunStatus

log = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])

MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_EVENTS = 500
SIGNATURE_HEADER = "X-Nextix-Signature"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def signature_ok(secret: str | None, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    return hmac.compare_digest(sign(secret, body), header)


def _int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


@router.post("/internal/runs/{run_id}/events", status_code=status.HTTP_202_ACCEPTED)
async def run_events(
    run_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    gh: Annotated[GitHubClient, Depends(get_github)],
    publisher: Annotated[EventPublisher, Depends(get_publisher)],
) -> dict[str, Any]:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "event batch too large")

    run = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown run")
    if run.status in TERMINAL_STATUSES:
        # Finished or cancelled: tell the runner to stop. Checked before the signature
        # because a finished run's secret has already been cleared.
        raise HTTPException(status.HTTP_410_GONE, f"run is {run.status}")
    if not signature_ok(run.callback_secret, body, request.headers.get(SIGNATURE_HEADER)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad signature")

    try:
        data = json.loads(body)
        raw_events = data["events"]
        assert isinstance(raw_events, list)
    except (ValueError, KeyError, AssertionError, TypeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "expected {'events': [...]}") from exc
    if len(raw_events) > MAX_EVENTS:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "too many events")

    now = datetime.now(UTC)
    run.last_heartbeat = now  # any authenticated batch proves the sandbox is alive
    to_store: list[tuple[str, dict[str, Any]]] = []
    start_running = False
    for item in raw_events:
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str):
            continue
        kind = item["kind"]
        raw_payload = item.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        if kind == "heartbeat":
            continue
        if kind == "state":
            if payload.get("status") == RunStatus.RUNNING and run.status == RunStatus.CLAIMED:
                start_running = True
            continue  # the transition itself records the state event
        if kind == "usage":
            run.input_tokens = max(run.input_tokens, _int(payload.get("input_tokens")))
            run.output_tokens = max(run.output_tokens, _int(payload.get("output_tokens")))
            cost = payload.get("cost_usd")
            if isinstance(cost, int | float) and cost >= 0:
                run.cost_usd = max(run.cost_usd, Decimal(str(round(cost, 4))))
        to_store.append((kind, payload))

    # Commits the heartbeat/usage updates along with the transcript rows.
    stored = await store_events(session, publisher, run.id, to_store)
    if start_running:
        await record_transition(session, run, RunStatus.RUNNING, gh=gh, publisher=publisher)
    else:
        # Even a bare heartbeat is news: the board shows "No heartbeat" after 30 s without one.
        await publish_run(publisher, run)
        await publish_ticket_changes(session, publisher, {run.ticket_id})
    return {"stored": len(stored)}
