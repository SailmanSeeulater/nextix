"""Ticket state: mirroring GitHub into our DB and deriving the board column.

GitHub is the source of truth. Everything here either copies GitHub state into
``repos``/``tickets`` or derives a view from it; nothing invents state.
"""

import re
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Subquery, exists, func, select, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from nextix.config import get_settings
from nextix.db.models import Repo, Run, Ticket
from nextix.github.schemas import GhIssue, GhPullRequest, GhRepository
from nextix.runs.state import ACTIVE_STATUSES, FAILED_STATUSES, RunStatus
from nextix.tickets.schemas import Column, RunSummary, TicketCard

NEXTIX_LABEL = "nextix"
NEEDS_INPUT_LABEL = "nextix:needs-input"
BRANCH_PREFIX = "nextix/issue-"
ISSUE_GONE = "deleted"  # issue_state for deleted or transferred issues

_BRANCH_RE = re.compile(r"^nextix/issue-(\d+)$")


# --------------------------------------------------------------------------- columns


@dataclass(frozen=True)
class TicketView:
    issue_state: str | None
    labels: Sequence[str]


@dataclass(frozen=True)
class RunView:
    status: str


@dataclass(frozen=True)
class PrView:
    state: str  # open | closed | merged


def derive_column(ticket: TicketView, latest_run: RunView | None, pr: PrView | None) -> Column:
    """Pure mapping from mirrored GitHub state + latest run to a board column.

    Precedence, first match wins:
      1. Done         PR merged, or issue closed (merged or not)
      2. Doing        an active run (queued / claimed / running)
      3. Needs Input  needs-input label, or the latest run ended in needs_input
      4. In Review    an open PR from the ticket's branch
      5. Failed       latest run failed / timed out / cancelled (no open PR, by order)
      6. Todo         everything else
    """
    if (pr is not None and pr.state == "merged") or ticket.issue_state == "closed":
        return Column.DONE
    status = latest_run.status if latest_run else None
    if status in ACTIVE_STATUSES:
        return Column.DOING
    if NEEDS_INPUT_LABEL in ticket.labels or status == RunStatus.NEEDS_INPUT:
        return Column.NEEDS_INPUT
    if pr is not None and pr.state == "open":
        return Column.IN_REVIEW
    if status in FAILED_STATUSES:
        return Column.FAILED
    return Column.TODO


def is_on_board(ticket: Ticket, repo: Repo) -> bool:
    return repo.enabled and NEXTIX_LABEL in ticket.labels and ticket.issue_state != ISSUE_GONE


def issue_number_from_branch(ref: str) -> int | None:
    match = _BRANCH_RE.match(ref)
    return int(match.group(1)) if match else None


# --------------------------------------------------------------------------- repos


async def upsert_repo(
    session: AsyncSession, *, owner: str, name: str, installation_id: int, default_branch: str
) -> Repo:
    """Insert or refresh a repo. Never changes ``enabled`` on an existing row."""
    stmt = (
        insert(Repo)
        .values(
            id=uuid.uuid4(),
            owner=owner,
            name=name,
            installation_id=installation_id,
            default_branch=default_branch,
        )
        .on_conflict_do_update(
            constraint="uq_repos_owner_name",
            set_={"installation_id": installation_id, "default_branch": default_branch},
        )
        .returning(Repo.id)
    )
    repo_id = (await session.execute(stmt)).scalar_one()
    repo = await session.get(Repo, repo_id, populate_existing=True)
    assert repo is not None
    return repo


async def get_repo(session: AsyncSession, owner: str, name: str) -> Repo | None:
    return await session.scalar(select(Repo).where(Repo.owner == owner, Repo.name == name))


async def set_repos_enabled(
    session: AsyncSession, *, enabled: bool, installation_id: int, full_names: Sequence[str] = ()
) -> None:
    """Enable/disable repos of an installation (all of them, or only ``full_names``)."""
    stmt = update(Repo).where(Repo.installation_id == installation_id)
    if full_names:
        stmt = stmt.where((Repo.owner + "/" + Repo.name).in_(list(full_names)))
    await session.execute(stmt.values(enabled=enabled))


@dataclass
class RepoReconcileResult:
    active: int = 0
    disabled: int = 0
    deleted: int = 0

    def __str__(self) -> str:
        return f"{self.active} repos active, {self.disabled} disabled, {self.deleted} removed"


async def retire_repos(
    session: AsyncSession, installation_id: int, *, keep: Collection[str] = ()
) -> RepoReconcileResult:
    """Retire an installation's repos except ``keep`` (full names, case-insensitive).

    Repos with tickets are disabled so their history survives. Repos that never had
    a ticket are deleted outright, since nothing about them is worth keeping.
    """
    keep_lower = {name.lower() for name in keep}
    result = RepoReconcileResult()
    repos = await session.scalars(select(Repo).where(Repo.installation_id == installation_id))
    for repo in repos:
        if repo.full_name.lower() in keep_lower:
            continue
        has_tickets = await session.scalar(select(exists().where(Ticket.repo_id == repo.id)))
        if has_tickets:
            repo.enabled = False
            result.disabled += 1
        else:
            await session.delete(repo)
            result.deleted += 1
    await session.flush()
    return result


async def reconcile_installation_repos(
    session: AsyncSession,
    installation_id: int,
    accessible: Sequence[GhRepository],
    *,
    enable: Collection[str] = (),
) -> RepoReconcileResult:
    """Make our repos for an installation match the repos GitHub says it can access.

    ``accessible`` must be the installation's full current repo list from GitHub.
    Webhook payloads are not enough: switching an installation from "all
    repositories" to "selected" lists only the added repos, never the dropped ones.
    ``enable`` names repos to switch back on (ones the user just added).
    """
    for gh_repo in accessible:
        await upsert_repo(
            session,
            owner=gh_repo.owner.login,
            name=gh_repo.name,
            installation_id=installation_id,
            default_branch=gh_repo.default_branch,
        )
    if enable:
        await set_repos_enabled(
            session, enabled=True, installation_id=installation_id, full_names=list(enable)
        )
    result = await retire_repos(session, installation_id, keep=[r.full_name for r in accessible])
    result.active = len(accessible)
    return result


# --------------------------------------------------------------------------- tickets


async def get_ticket(session: AsyncSession, repo: Repo, issue_number: int) -> Ticket | None:
    return await session.scalar(
        select(Ticket).where(Ticket.repo_id == repo.id, Ticket.issue_number == issue_number)
    )


async def upsert_ticket_from_issue(
    session: AsyncSession, repo: Repo, issue: GhIssue, *, created_via: str
) -> uuid.UUID:
    """Mirror an issue's title/body/state/labels. ``created_via`` is set on insert only."""
    fields = {
        "title": issue.title,
        "body": issue.body,
        "issue_state": issue.state,
        "labels": issue.label_names,
    }
    stmt = (
        insert(Ticket)
        .values(
            id=uuid.uuid4(),
            repo_id=repo.id,
            issue_number=issue.number,
            created_via=created_via,
            **fields,
        )
        .on_conflict_do_update(
            constraint="uq_tickets_repo_id_issue_number",
            set_={**fields, "updated_at": func.now()},
        )
        .returning(Ticket.id)
    )
    return (await session.execute(stmt)).scalar_one()


async def mark_issue_gone(session: AsyncSession, repo: Repo, issue_number: int) -> uuid.UUID | None:
    ticket = await get_ticket(session, repo, issue_number)
    if ticket is None:
        return None
    ticket.issue_state = ISSUE_GONE
    ticket.updated_at = datetime.now(UTC)
    return ticket.id


async def link_pull_request(
    session: AsyncSession, repo: Repo, pr: GhPullRequest
) -> uuid.UUID | None:
    """Attach a ``nextix/issue-<n>`` PR to its ticket and mirror its state.

    Ignores PRs from forks and PRs for issues we don't track. An update about an
    older, non-open PR never overwrites a different PR that is currently open.
    """
    issue_number = issue_number_from_branch(pr.head.ref)
    if issue_number is None:
        return None
    if pr.head.repo is None or pr.head.repo.full_name != repo.full_name:
        return None
    ticket = await get_ticket(session, repo, issue_number)
    if ticket is None:
        return None
    state = pr.mirrored_state
    if (
        ticket.pr_number is not None
        and ticket.pr_number != pr.number
        and ticket.pr_state == "open"
        and state != "open"
    ):
        return None
    ticket.pr_number = pr.number
    ticket.pr_state = state
    ticket.updated_at = datetime.now(UTC)
    return ticket.id


# --------------------------------------------------------------------------- board


def _latest_runs_subquery() -> Subquery:
    return (
        select(Run)
        .ext(distinct_on(Run.ticket_id))
        .order_by(Run.ticket_id, Run.attempt.desc())
        .subquery("latest_run")
    )


def _card(ticket: Ticket, repo: Repo, run: Run | None) -> TicketCard:
    web = get_settings().github_web_url.rstrip("/")
    base = f"{web}/{repo.full_name}"
    column = derive_column(
        TicketView(issue_state=ticket.issue_state, labels=ticket.labels),
        RunView(status=run.status) if run else None,
        PrView(state=ticket.pr_state) if ticket.pr_state else None,
    )
    return TicketCard(
        id=ticket.id,
        repo=repo.full_name,
        issue_number=ticket.issue_number,
        title=ticket.title,
        labels=list(ticket.labels),
        issue_state=ticket.issue_state,
        issue_url=f"{base}/issues/{ticket.issue_number}",
        pr_number=ticket.pr_number,
        pr_state=ticket.pr_state,
        pr_url=f"{base}/pull/{ticket.pr_number}" if ticket.pr_number else None,
        column=column,
        latest_run=(
            RunSummary(
                id=run.id,
                attempt=run.attempt,
                status=run.status,
                agent_id=run.agent_id,
                started_at=run.started_at,
                last_heartbeat=run.last_heartbeat,
                cost_usd=float(run.cost_usd),
            )
            if run
            else None
        ),
        created_via=ticket.created_via,
        updated_at=ticket.updated_at,
    )


async def list_board(
    session: AsyncSession, *, repo: str | None = None, column: Column | None = None
) -> list[TicketCard]:
    latest = aliased(Run, _latest_runs_subquery())
    stmt = (
        select(Ticket, Repo, latest)
        .join(Repo, Ticket.repo_id == Repo.id)
        .outerjoin(latest, latest.ticket_id == Ticket.id)
        .where(
            Repo.enabled.is_(True),
            Ticket.labels.contains([NEXTIX_LABEL]),
            Ticket.issue_state.is_distinct_from(ISSUE_GONE),
        )
        .order_by(Ticket.updated_at.desc())
        .execution_options(populate_existing=True)
    )
    if repo:
        owner, _, name = repo.partition("/")
        stmt = stmt.where(Repo.owner == owner, Repo.name == name)
    cards = [_card(t, r, run) for t, r, run in (await session.execute(stmt)).all()]
    if column is not None:
        cards = [c for c in cards if c.column == column]
    return cards


async def get_card(session: AsyncSession, ticket_id: uuid.UUID) -> tuple[TicketCard, bool] | None:
    """The card for one ticket, plus whether it currently belongs on the board."""
    latest = aliased(Run, _latest_runs_subquery())
    row = (
        await session.execute(
            select(Ticket, Repo, latest)
            .join(Repo, Ticket.repo_id == Repo.id)
            .outerjoin(latest, latest.ticket_id == Ticket.id)
            .where(Ticket.id == ticket_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        return None
    ticket, repo, run = row
    return _card(ticket, repo, run), is_on_board(ticket, repo)
