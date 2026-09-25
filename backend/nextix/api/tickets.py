"""Board data."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.api.auth import require_user
from nextix.db.session import get_db
from nextix.tickets.schemas import Column, TicketCard
from nextix.tickets.service import list_board

router = APIRouter(tags=["tickets"], dependencies=[Depends(require_user)])


@router.get("/tickets", response_model=list[TicketCard])
async def get_tickets(
    session: Annotated[AsyncSession, Depends(get_db)],
    repo: Annotated[str | None, Query(description="owner/name")] = None,
    column: Annotated[Column | None, Query()] = None,
) -> list[TicketCard]:
    return await list_board(session, repo=repo, column=column)
