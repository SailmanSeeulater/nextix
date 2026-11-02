"""Board data and ticket creation."""

import hashlib
import logging
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.api.auth import require_user
from nextix.api.deps import get_enqueuer, get_github, get_publisher, get_triager
from nextix.config import Settings, get_settings
from nextix.db.models import Repo, Ticket, TicketCreation
from nextix.db.session import get_db
from nextix.events.stream import EventPublisher, publish_ticket_changes
from nextix.github.client import GitHubClient
from nextix.runs.lifecycle import ActiveRunExists, RunEnqueuer, enqueue_run
from nextix.tickets.creation import TicketCreationError, create_ticket
from nextix.tickets.schemas import (
    Column,
    CommentPosted,
    CommentRequest,
    CreateTicketRequest,
    CreateTicketResponse,
    TicketCard,
    TicketComment,
    TicketConversation,
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


IDEMPOTENCY_HEADER = "Idempotency-Key"
MAX_IDEMPOTENCY_KEY_CHARS = 128


def request_hash(body: CreateTicketRequest) -> str:
    """Fingerprint of the request, so one key can't be reused for a different ticket."""
    return hashlib.sha256(body.model_dump_json().encode()).hexdigest()


async def _reserve_key(session: AsyncSession, key: str, digest: str) -> CreateTicketResponse | None:
    """Claim `key` for this request, or return the earlier attempt's response.

    Committed on its own before the slow part starts, so a retry that arrives while
    triage is still running sees the reservation instead of starting a second one.
    """
    reserved = await session.execute(
        pg_insert(TicketCreation)
        .values(key=key, request_hash=digest)
        .on_conflict_do_nothing(index_elements=[TicketCreation.key])
        .returning(TicketCreation.key)
    )
    if reserved.scalar_one_or_none() is not None:
        await session.commit()
        return None
    await session.rollback()
    earlier = await session.get(TicketCreation, key)
    assert earlier is not None
    if earlier.request_hash != digest:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{IDEMPOTENCY_HEADER} {key!r} was already used for a different request.",
        )
    if earlier.response is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That request is still being processed; the first attempt will answer.",
        )
    return CreateTicketResponse.model_validate(earlier.response)


@router.post("/tickets", response_model=CreateTicketResponse, status_code=status.HTTP_201_CREATED)
async def post_ticket(
    body: CreateTicketRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    gh: Annotated[GitHubClient, Depends(get_github)],
    triager: Annotated[Triager | None, Depends(get_triager)],
    publisher: Annotated[EventPublisher, Depends(get_publisher)],
    settings: Annotated[Settings, Depends(get_settings)],
    enqueue: Annotated[RunEnqueuer, Depends(get_enqueuer)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias=IDEMPOTENCY_HEADER,
            min_length=1,
            max_length=MAX_IDEMPOTENCY_KEY_CHARS,
            description="Any client-chosen string; resending it returns the first result.",
        ),
    ] = None,
) -> CreateTicketResponse:
    digest = request_hash(body)
    if idempotency_key is not None:
        replay = await _reserve_key(session, idempotency_key, digest)
        if replay is not None:
            response.headers["Idempotent-Replayed"] = "true"
            return replay
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
    except (TicketCreationError, TriageError, httpx.HTTPStatusError) as exc:
        # Nothing was created, so the key is free for the client's next try.
        await session.rollback()
        if idempotency_key is not None:
            await session.execute(
                delete(TicketCreation).where(TicketCreation.key == idempotency_key)
            )
            await session.commit()
        if isinstance(exc, TicketCreationError):
            raise HTTPException(exc.status_code, exc.message) from exc
        if isinstance(exc, TriageError):
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Triage failed: {exc}. Nothing was created on GitHub. "
                "Try again, or create the ticket without triage.",
            ) from exc
        log.warning("GitHub error creating ticket: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"GitHub returned {exc.response.status_code} while creating the issue.",
        ) from exc
    await session.commit()

    await publish_ticket_changes(session, publisher, {created.ticket_id})
    if not created.needs_input:
        ticket = await session.get(Ticket, created.ticket_id)
        if ticket is not None:
            try:
                await enqueue_run(
                    session, ticket, trigger="initial", gh=gh, publisher=publisher, enqueue=enqueue
                )
            except ActiveRunExists:
                pass  # the issues.labeled webhook got there first
            except Exception:
                # The ticket exists on GitHub either way; the owner can start it from the board.
                log.exception("could not queue the first run for ticket %s", created.ticket_id)
    found = await get_card(session, created.ticket_id)
    assert found is not None
    card, _ = found
    result = CreateTicketResponse(
        ticket=card,
        issue_url=card.issue_url,
        board_url=settings.nextix_public_url.rstrip("/") + "/",
        needs_input=created.needs_input,
        clarifying_question=created.clarifying_question,
    )
    if idempotency_key is not None:
        # The issue exists now; from here on the key answers with this exact response.
        await session.execute(
            update(TicketCreation)
            .where(TicketCreation.key == idempotency_key)
            .values(ticket_id=created.ticket_id, response=result.model_dump(mode="json"))
        )
        await session.commit()
    return result


async def _ticket_and_repo(session: AsyncSession, ticket_id: uuid.UUID) -> tuple[Ticket, Repo]:
    ticket = await session.get(Ticket, ticket_id)
    repo = await session.get(Repo, ticket.repo_id) if ticket is not None else None
    if ticket is None or repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such ticket")
    return ticket, repo


def _github_failed(exc: httpx.HTTPStatusError, doing: str) -> HTTPException:
    log.warning("GitHub error %s: %s", doing, exc)
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        f"GitHub returned {exc.response.status_code} while {doing}.",
    )


@router.get("/tickets/{ticket_id}/conversation", response_model=TicketConversation)
async def get_conversation(
    ticket_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    gh: Annotated[GitHubClient, Depends(get_github)],
) -> TicketConversation:
    """The issue's body and comments, read from GitHub, which has the only copy."""
    ticket, repo = await _ticket_and_repo(session, ticket_id)
    try:
        issue = await gh.get_issue(repo.installation_id, repo.owner, repo.name, ticket.issue_number)
        comments = await gh.list_issue_comments(
            repo.installation_id, repo.owner, repo.name, ticket.issue_number, since=None
        )
    except httpx.HTTPStatusError as exc:
        raise _github_failed(exc, "reading the issue") from exc
    found = await get_card(session, ticket_id)
    if issue is None or found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the issue is gone from GitHub")
    return TicketConversation(
        ticket=found[0],
        body=issue.body or "",
        comments=[
            TicketComment(
                author=comment.user.login if comment.user else None,
                body=comment.body or "",
                created_at=comment.created_at,
                url=comment.html_url,
            )
            for comment in comments
        ],
    )


@router.post(
    "/tickets/{ticket_id}/comments",
    response_model=CommentPosted,
    status_code=status.HTTP_201_CREATED,
)
async def post_comment(
    ticket_id: uuid.UUID,
    body: CommentRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    gh: Annotated[GitHubClient, Depends(get_github)],
) -> CommentPosted:
    """Comment on the ticket's issue as the nexTix app. The app's own comments never start
    an agent run, so this is safe for agents to call."""
    ticket, repo = await _ticket_and_repo(session, ticket_id)
    try:
        url = await gh.create_comment(
            repo.installation_id, repo.owner, repo.name, ticket.issue_number, body.body
        )
    except httpx.HTTPStatusError as exc:
        raise _github_failed(exc, "posting the comment") from exc
    return CommentPosted(url=url)
