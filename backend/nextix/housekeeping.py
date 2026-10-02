"""Periodic cleanup, run by Celery beat on the default queue.

`nextix.prune_webhook_deliveries` (daily) drops old webhook dedupe rows. The table only
needs to outlive GitHub's redelivery window (deliveries can be redelivered for a few
days), so a month of history is plenty and the table stops growing forever. The same
task drops old `ticket_creations` idempotency rows, which only need to outlive a client's
retry (minutes), so a day is generous, and it prunes the `nextix/screenshots` branch of
each repo: a ticket's before/after images are only useful while its PR is open, so once
the PR is merged or closed (or the issue closed) the folder goes, one commit per repo.
"""

import asyncio
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from nextix.celery_app import celery_app
from nextix.config import get_settings
from nextix.db.models import Repo, Ticket, TicketCreation, WebhookDelivery
from nextix.github.app_auth import GitHubAppAuth
from nextix.github.client import GitHubClient
from nextix.review.pr_screenshots import SCREENSHOTS_BRANCH

log = logging.getLogger(__name__)

WEBHOOK_DELIVERY_RETENTION = timedelta(days=30)
TICKET_CREATION_RETENTION = timedelta(days=1)
GITHUB_HTTP_TIMEOUT_S = 20.0

# Folder per ticket on the screenshots branch (see review/pr_screenshots.py).
_ISSUE_FOLDER = re.compile(r"^issue-(\d+)/")


def stale_screenshot_paths(paths: list[str], finished_issues: set[int]) -> list[str]:
    """The screenshot files that belong to tickets whose PR is finished."""
    stale = []
    for path in paths:
        m = _ISSUE_FOLDER.match(path)
        if m and int(m.group(1)) in finished_issues:
            stale.append(path)
    return stale


async def prune_pr_screenshots(session: AsyncSession, gh: GitHubClient) -> int:
    """Delete finished tickets' folders from every repo's screenshots branch.

    Best effort per repo: a GitHub error is logged and the next repo is still handled.
    Returns how many files were removed.
    """
    removed = 0
    repos = (await session.scalars(select(Repo).where(Repo.enabled))).all()
    for repo in repos:
        try:
            paths = await gh.list_file_paths(
                repo.installation_id, repo.owner, repo.name, ref=SCREENSHOTS_BRANCH
            )
            if not paths:
                continue
            numbers = {int(m.group(1)) for p in paths if (m := _ISSUE_FOLDER.match(p))}
            finished = set(
                await session.scalars(
                    select(Ticket.issue_number).where(
                        Ticket.repo_id == repo.id,
                        Ticket.issue_number.in_(numbers),
                        (Ticket.pr_state.in_(["merged", "closed"]))
                        | (Ticket.issue_state == "closed"),
                    )
                )
            )
            stale = stale_screenshot_paths(paths, finished)
            if not stale:
                continue
            by_issue: dict[int, int] = defaultdict(int)
            for p in stale:
                by_issue[int(_ISSUE_FOLDER.match(p).group(1))] += 1  # type: ignore[union-attr]
            await gh.commit_files(
                repo.installation_id,
                repo.owner,
                repo.name,
                branch=SCREENSHOTS_BRANCH,
                files={},
                remove=stale,
                message="nextix: remove screenshots of finished tickets "
                + ", ".join(f"#{n}" for n in sorted(by_issue)),
            )
            removed += len(stale)
            log.info(
                "%s: removed screenshots of %d finished tickets", repo.full_name, len(by_issue)
            )
        except (httpx.HTTPError, RuntimeError):
            log.exception("%s: could not prune the screenshots branch", repo.full_name)
    return removed


async def prune_webhook_deliveries(
    session: AsyncSession, *, older_than: timedelta = WEBHOOK_DELIVERY_RETENTION
) -> int:
    """Delete deliveries received more than ``older_than`` ago. Returns how many."""
    cutoff = datetime.now(UTC) - older_than
    result = await session.execute(
        delete(WebhookDelivery).where(WebhookDelivery.received_at < cutoff)
    )
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def prune_ticket_creations(
    session: AsyncSession, *, older_than: timedelta = TICKET_CREATION_RETENTION
) -> int:
    """Delete idempotency records older than ``older_than``. Returns how many."""
    cutoff = datetime.now(UTC) - older_than
    result = await session.execute(delete(TicketCreation).where(TicketCreation.created_at < cutoff))
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def _prune() -> tuple[int, int, int]:
    # Each task runs in its own event loop (asyncio.run), and pooled connections can't
    # cross loops, so this gets a throwaway engine like the run tasks do.
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    http = httpx.AsyncClient(timeout=GITHUB_HTTP_TIMEOUT_S)
    try:
        gh = GitHubClient(
            GitHubAppAuth.from_settings(settings, http), http, settings.github_api_url
        )
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return (
                await prune_webhook_deliveries(session),
                await prune_ticket_creations(session),
                await prune_pr_screenshots(session, gh),
            )
    finally:
        await http.aclose()
        await engine.dispose()


@celery_app.task(name="nextix.prune_webhook_deliveries", ignore_result=True)
def prune_webhook_deliveries_task() -> int:
    deliveries, creations, screenshots = asyncio.run(_prune())
    log.info(
        "pruned %d webhook deliveries older than %s, %d ticket creations older than %s, "
        "and %d screenshot files of finished tickets",
        deliveries,
        WEBHOOK_DELIVERY_RETENTION,
        creations,
        TICKET_CREATION_RETENTION,
        screenshots,
    )
    return deliveries + creations + screenshots
