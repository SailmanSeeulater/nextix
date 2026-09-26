"""CI check runs for a ticket's pull request (docs/phase4.md, decision 7).

Check runs arrive through `check_run` webhooks and are also read from GitHub when the
ticket page is opened (at most once a minute per ticket), so the Checks tab is right even
when a webhook was missed. Both need the GitHub App's "Checks: read" permission; without
it GitHub answers 403 and the ticket remembers `checks_error = "forbidden"`.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.db.models import CheckRun, Repo, Ticket
from nextix.github.client import GitHubClient
from nextix.github.schemas import GhCheckRun
from nextix.tickets.service import issue_number_from_branch

log = logging.getLogger(__name__)

SYNC_INTERVAL = timedelta(seconds=60)
FORBIDDEN = "forbidden"


async def upsert_check_run(session: AsyncSession, repo: Repo, check: GhCheckRun) -> None:
    values: dict[str, Any] = {
        "repo_id": repo.id,
        "head_sha": check.head_sha,
        "head_branch": check.check_suite.head_branch if check.check_suite else None,
        "name": check.name[:200],
        "status": check.status,
        "conclusion": check.conclusion,
        "html_url": check.html_url,
        "details_url": check.details_url,
        "app_name": check.app.name if check.app else None,
        "started_at": check.started_at,
        "completed_at": check.completed_at,
        "updated_at": datetime.now(UTC),
    }
    stmt = insert(CheckRun).values(id=check.id, **values)
    await session.execute(stmt.on_conflict_do_update(index_elements=[CheckRun.id], set_=values))


async def tickets_for_check(session: AsyncSession, repo: Repo, check: GhCheckRun) -> set[uuid.UUID]:
    """Tickets whose PR this check belongs to: by head sha, or by the nextix branch."""
    found = set(
        await session.scalars(
            select(Ticket.id).where(Ticket.repo_id == repo.id, Ticket.pr_head_sha == check.head_sha)
        )
    )
    branch = check.check_suite.head_branch if check.check_suite else None
    number = issue_number_from_branch(branch) if branch else None
    if number is not None:
        found |= set(
            await session.scalars(
                select(Ticket.id).where(Ticket.repo_id == repo.id, Ticket.issue_number == number)
            )
        )
    return found


async def sync_checks(
    session: AsyncSession, gh: GitHubClient, repo: Repo, ticket: Ticket, *, force: bool = False
) -> None:
    """Read the PR head's check runs from GitHub, unless that was done in the last minute."""
    sha = ticket.pr_head_sha
    if not sha:
        return
    now = datetime.now(UTC)
    if not force and ticket.checks_synced_at and now - ticket.checks_synced_at < SYNC_INTERVAL:
        return
    ticket.checks_synced_at = now
    try:
        checks = await gh.list_check_runs(repo.installation_id, repo.owner, repo.name, sha)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            ticket.checks_error = FORBIDDEN
        else:
            log.warning("could not read checks for %s@%s: %s", repo.full_name, sha[:7], exc)
        await session.commit()
        return
    except httpx.HTTPError as exc:
        log.warning("could not read checks for %s@%s: %s", repo.full_name, sha[:7], exc)
        await session.commit()
        return
    for check in checks:
        await upsert_check_run(session, repo, check)
    ticket.checks_error = None
    await session.commit()


async def checks_json(session: AsyncSession, ticket: Ticket) -> dict[str, Any]:
    """{connected, runs}: the newest check run per name on the PR's head commit."""
    runs: list[dict[str, Any]] = []
    if ticket.pr_head_sha:
        rows = await session.scalars(
            select(CheckRun)
            .where(CheckRun.repo_id == ticket.repo_id, CheckRun.head_sha == ticket.pr_head_sha)
            .order_by(CheckRun.name, CheckRun.id.desc())
        )
        seen: set[str] = set()
        for row in rows:
            if row.name in seen:
                continue  # a re-run: keep the newest
            seen.add(row.name)
            runs.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "status": row.status,
                    "conclusion": row.conclusion,
                    "html_url": row.html_url,
                    "app_name": row.app_name,
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                }
            )
    return {"connected": ticket.checks_error != FORBIDDEN, "runs": runs}
