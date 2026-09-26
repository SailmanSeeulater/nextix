"""Review surface reads: a run's artifacts and a ticket's PR diff (docs/phase4.md)."""

import logging
import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.api.auth import require_user
from nextix.api.deps import get_github
from nextix.config import Settings, get_settings
from nextix.db.models import Artifact, Repo, Ticket
from nextix.db.session import get_db
from nextix.github.client import DiffTooLarge, GitHubClient
from nextix.runs.artifacts import CONTENT_TYPES, resolve_path

log = logging.getLogger(__name__)

router = APIRouter(tags=["review"], dependencies=[Depends(require_user)])

MAX_DIFF_BYTES = 2 * 1024 * 1024


@router.get("/artifacts/{artifact_id}")
async def get_artifact(
    artifact_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    artifact = await session.get(Artifact, artifact_id)
    path = resolve_path(settings.artifact_dir, artifact) if artifact else None
    if artifact is None or path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such artifact")
    return FileResponse(
        path,
        media_type=CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
        # Artifacts never change once stored.
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/tickets/{ticket_id}/diff")
async def get_diff(
    ticket_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    gh: Annotated[GitHubClient, Depends(get_github)],
) -> dict[str, Any]:
    """The PR's unified diff, from GitHub (the source of truth), capped at 2 MB."""
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such ticket")
    repo = await session.get(Repo, ticket.repo_id)
    if repo is None or ticket.pr_number is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this ticket has no pull request yet")
    try:
        diff, truncated = await gh.get_pull_diff(
            repo.installation_id, repo.owner, repo.name, ticket.pr_number, max_bytes=MAX_DIFF_BYTES
        )
    except DiffTooLarge:
        diff, truncated = "", True
    except httpx.HTTPError as exc:
        log.warning("could not read the diff of %s#%s: %s", repo.full_name, ticket.pr_number, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "GitHub didn't return the diff") from exc
    return {
        "pr_number": ticket.pr_number,
        "head_sha": ticket.pr_head_sha,
        "diff": diff,
        "truncated": truncated,
    }
