"""Connected repositories."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.api.auth import require_user
from nextix.db.models import Repo
from nextix.db.session import get_db

router = APIRouter(tags=["repos"], dependencies=[Depends(require_user)])


class RepoOut(BaseModel):
    id: uuid.UUID
    full_name: str
    default_branch: str


@router.get("/repos", response_model=list[RepoOut])
async def list_repos(session: Annotated[AsyncSession, Depends(get_db)]) -> list[RepoOut]:
    """Enabled repos, the ones tickets can be filed against."""
    repos = await session.scalars(
        select(Repo).where(Repo.enabled.is_(True)).order_by(Repo.owner, Repo.name)
    )
    return [RepoOut(id=r.id, full_name=r.full_name, default_branch=r.default_branch) for r in repos]
