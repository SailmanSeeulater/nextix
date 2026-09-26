"""POST /api/github/webhook: verify, dedupe, dispatch, publish."""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.api.deps import get_enqueuer, get_github, get_publisher
from nextix.config import Settings, get_settings
from nextix.db.models import Run, Ticket
from nextix.db.session import get_db
from nextix.events.stream import EventPublisher, publish_ticket_changes
from nextix.github.client import GitHubClient
from nextix.github.webhooks import dispatch, record_delivery, verify_signature
from nextix.runs.lifecycle import ActiveRunExists, RunEnqueuer, enqueue_run

log = logging.getLogger(__name__)

router = APIRouter(tags=["github"])


@router.post("/github/webhook")
async def github_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    gh: Annotated[GitHubClient, Depends(get_github)],
    publisher: Annotated[EventPublisher, Depends(get_publisher)],
    settings: Annotated[Settings, Depends(get_settings)],
    enqueue: Annotated[RunEnqueuer, Depends(get_enqueuer)],
) -> dict[str, Any]:
    body = await request.body()
    if not verify_signature(
        settings.github_webhook_secret, body, request.headers.get("X-Hub-Signature-256")
    ):
        raise HTTPException(status_code=401, detail="invalid signature")

    delivery_id = request.headers.get("X-GitHub-Delivery")
    event = request.headers.get("X-GitHub-Event")
    if not delivery_id or not event:
        raise HTTPException(status_code=400, detail="missing GitHub delivery headers")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body is not a JSON object")

    action = payload.get("action")
    # Dedupe row and handler effects commit together: a crash mid-handler rolls
    # both back, so GitHub's redelivery is processed instead of skipped.
    if not await record_delivery(session, delivery_id, event, action):
        return {"status": "duplicate", "delivery": delivery_id}
    result = await dispatch(event, payload, session, gh)
    await session.commit()

    log.info("webhook %s %s.%s: %s", delivery_id, event, action, result.note)
    await publish_ticket_changes(session, publisher, result.changed_tickets)
    for ticket_id in result.start_runs:
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None:
            continue
        earlier = await session.scalar(
            select(func.count()).select_from(Run).where(Run.ticket_id == ticket.id)
        )
        try:
            await enqueue_run(
                session,
                ticket,
                trigger="retry" if earlier else "initial",
                gh=gh,
                publisher=publisher,
                enqueue=enqueue,
            )
        except ActiveRunExists:
            log.info("ticket %s already has an active run", ticket_id)
        except Exception:
            log.exception("could not queue a run for ticket %s", ticket_id)
    return {"status": "ok", "note": result.note, "tickets": len(result.changed_tickets)}
