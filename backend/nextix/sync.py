"""Backfill one repo from GitHub: ``python -m nextix.sync owner/repo``.

Pulls every issue labeled ``nextix``, re-checks tickets we already track (their
label may have been removed while we weren't listening), and links PRs from
``nextix/issue-<n>`` branches. GitHub wins wherever the DB disagrees.
"""

import argparse
import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass

import httpx
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.config import get_settings
from nextix.db.models import Ticket
from nextix.db.session import get_async_sessionmaker
from nextix.events.stream import EventPublisher, RedisPublisher, publish_ticket_changes
from nextix.github.app_auth import GitHubAppAuth, GitHubAuthError, GitHubNotConfiguredError
from nextix.github.client import GitHubClient
from nextix.tickets import service

log = logging.getLogger("nextix.sync")


@dataclass
class SyncReport:
    repo: str
    issues: int = 0
    rechecked: int = 0
    prs_linked: int = 0

    def __str__(self) -> str:
        return (
            f"{self.repo}: {self.issues} labeled issues, {self.rechecked} tracked tickets "
            f"re-checked, {self.prs_linked} PRs linked"
        )


async def sync_repo(
    session: AsyncSession, gh: GitHubClient, full_name: str
) -> tuple[SyncReport, set[uuid.UUID]]:
    owner, _, name = full_name.partition("/")
    if not owner or not name:
        raise ValueError(f"expected owner/name, got {full_name!r}")
    report = SyncReport(repo=full_name)
    changed: set[uuid.UUID] = set()

    installation = await gh.get_repo_installation(owner, name)
    gh_repo = await gh.get_repo(installation.id, owner, name)
    repo = await service.upsert_repo(
        session,
        owner=gh_repo.owner.login,
        name=gh_repo.name,
        installation_id=installation.id,
        default_branch=gh_repo.default_branch,
    )

    seen: set[int] = set()
    async for issue in gh.list_issues(
        installation.id, repo.owner, repo.name, labels=service.NEXTIX_LABEL
    ):
        changed.add(
            await service.upsert_ticket_from_issue(session, repo, issue, created_via="github")
        )
        seen.add(issue.number)
        report.issues += 1

    tracked = await session.scalars(select(Ticket.issue_number).where(Ticket.repo_id == repo.id))
    for number in set(tracked) - seen:
        current = await gh.get_issue(installation.id, repo.owner, repo.name, number)
        if current is None:
            gone = await service.mark_issue_gone(session, repo, number)
            if gone:
                changed.add(gone)
        else:
            changed.add(
                await service.upsert_ticket_from_issue(session, repo, current, created_via="github")
            )
        report.rechecked += 1

    # Oldest first, so the newest PR for a branch is the one that sticks.
    pulls = [pr async for pr in gh.list_pulls(installation.id, repo.owner, repo.name)]
    for pr in sorted(pulls, key=lambda p: p.number):
        ticket_id = await service.link_pull_request(session, repo, pr)
        if ticket_id:
            changed.add(ticket_id)
            report.prs_linked += 1

    return report, changed


async def run(full_name: str, *, gh: GitHubClient, publisher: EventPublisher | None) -> SyncReport:
    async with get_async_sessionmaker()() as session:
        report, changed = await sync_repo(session, gh, full_name)
        await session.commit()
        if publisher is not None:
            await publish_ticket_changes(session, publisher, changed)
    return report


async def _main(full_name: str) -> int:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as http:
        gh = GitHubClient(
            GitHubAppAuth.from_settings(settings, http), http, settings.github_api_url
        )
        redis = aioredis.Redis.from_url(settings.redis_url)
        try:
            report = await run(full_name, gh=gh, publisher=RedisPublisher(redis))
        finally:
            await redis.aclose()
    print(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nextix.sync", description=__doc__)
    parser.add_argument("repo", help="owner/name")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(_main(args.repo))
    except (GitHubNotConfiguredError, GitHubAuthError, ValueError) as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        hint = " (is the GitHub App installed on this repo?)" if status == 404 else ""
        print(f"sync failed: GitHub returned {status} for {exc.request.url}{hint}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
