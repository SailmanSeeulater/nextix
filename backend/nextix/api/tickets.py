"""Board data and ticket creation."""

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.api.auth import require_user
from nextix.api.deps import get_github, get_publisher, get_triager
from nextix.config import Settings, get_settings
from nextix.db.session import get_db
from nextix.events.stream import EventPublisher, publish_ticket_changes
from nextix.github.client import GitHubClient
from nextix.tickets.creation import TicketCreationError, create_ticket
from nextix.tickets.schemas import (
    Column,
    CreateTicketRequest,
    CreateTicketResponse,
    TicketCard,
)
from nextix.tickets.service import get_card, list_board
from nextix.tickets.triage import TriageError, Triager

log = logging.getLogger(__name__)

router = APIRouter(tags=["tickets"], dependencies=[Depends(require_user)])


@router.get("/tickets", response_model=list[TicketCard])
async def get_tickets(
    session: Annotated[AsyncSession, Depends(get_db)],
    repo: Annotated[str | None, Query(description="owner/name")] = None,
    column: Annotated[Column | None, Query()] = None,
) -> list[TicketCard]:
    return await list_board(session, repo=repo, column=column)


@router.post("/tickets", response_model=CreateTicketResponse, status_code=status.HTTP_201_CREATED)
async def post_ticket(
    body: CreateTicketRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    gh: Annotated[GitHubClient, Depends(get_github)],
    triager: Annotated[Triager | None, Depends(get_triager)],
    publisher: Annotated[EventPublisher, Depends(get_publisher)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CreateTicketResponse:
    try:
        created = await create_ticket(
            session,
            gh,
            triager,
            triage=body.triage,
            repo_full_name=body.repo,
            prompt=body.prompt,
            labels=body.labels,
            created_via=body.created_via,
        )
    except TicketCreationError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    except TriageError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Triage failed: {exc}. Nothing was created on GitHub. "
            "Try again, or create the ticket without triage.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        log.warning("GitHub error creating ticket: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"GitHub returned {exc.response.status_code} while creating the issue.",
        ) from exc
    await session.commit()

    # Phase 3: enqueue an initial run here when the ticket is actionable.
    await publish_ticket_changes(session, publisher, {created.ticket_id})
    found = await get_card(session, created.ticket_id)
    assert found is not None
    card, _ = found
    return CreateTicketResponse(
        ticket=card,
        issue_url=card.issue_url,
        board_url=settings.nextix_public_url.rstrip("/") + "/",
        needs_input=created.needs_input,
        clarifying_question=created.clarifying_question,
    )
