"""GitHub webhook signature verification and event dispatch.

Handlers mirror GitHub state into the DB and return the ticket IDs they changed,
so the caller can publish board events after commit.
"""

import hashlib
import hmac
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.db.models import Repo, Ticket, WebhookDelivery
from nextix.github.client import GitHubClient
from nextix.github.schemas import (
    GhInstallation,
    GhIssue,
    GhPullRequest,
    GhRepoRef,
    GhRepository,
)
from nextix.tickets import service

log = logging.getLogger(__name__)


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Check ``X-Hub-Signature-256``. Fails closed when no secret is configured."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


async def record_delivery(
    session: AsyncSession, delivery_id: str, event: str, action: str | None
) -> bool:
    """Insert the delivery row. Returns False if it was already processed."""
    stmt = (
        insert(WebhookDelivery)
        .values(delivery_id=delivery_id, event=event, action=action)
        .on_conflict_do_nothing(index_elements=["delivery_id"])
        .returning(WebhookDelivery.delivery_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


@dataclass
class DispatchResult:
    changed_tickets: set[uuid.UUID] = field(default_factory=set)
    note: str = "ok"


Handler = Callable[[dict[str, Any], AsyncSession, GitHubClient], Awaitable[DispatchResult]]


async def _repo_from_event(payload: dict[str, Any], session: AsyncSession) -> Repo | None:
    """Upsert the event's repository (every repo event carries the full object).

    Returns None when the repo is disabled or the payload has no installation.
    """
    if "repository" not in payload or "installation" not in payload:
        return None
    gh_repo = GhRepository.model_validate(payload["repository"])
    installation = GhInstallation.model_validate(payload["installation"])
    repo = await service.upsert_repo(
        session,
        owner=gh_repo.owner.login,
        name=gh_repo.name,
        installation_id=installation.id,
        default_branch=gh_repo.default_branch,
    )
    return repo if repo.enabled else None


async def handle_issues(
    payload: dict[str, Any], session: AsyncSession, gh: GitHubClient
) -> DispatchResult:
    repo = await _repo_from_event(payload, session)
    if repo is None:
        return DispatchResult(note="repo disabled or unknown")
    issue = GhIssue.model_validate(payload["issue"])
    action = payload.get("action")

    if action in ("deleted", "transferred"):
        gone = await service.mark_issue_gone(session, repo, issue.number)
        return DispatchResult(changed_tickets={gone} if gone else set())

    existing = await service.get_ticket(session, repo, issue.number)
    if existing is None and service.NEXTIX_LABEL not in issue.label_names:
        return DispatchResult(note="not a nextix issue")
    ticket_id = await service.upsert_ticket_from_issue(session, repo, issue, created_via="github")
    # Phase 3: newly labeled `nextix` by a trusted actor -> enqueue an initial run.
    return DispatchResult(changed_tickets={ticket_id})


async def handle_pull_request(
    payload: dict[str, Any], session: AsyncSession, gh: GitHubClient
) -> DispatchResult:
    repo = await _repo_from_event(payload, session)
    if repo is None:
        return DispatchResult(note="repo disabled or unknown")
    pr = GhPullRequest.model_validate(payload["pull_request"])
    ticket_id = await service.link_pull_request(session, repo, pr)
    if ticket_id is None:
        return DispatchResult(note="not a nextix branch")
    return DispatchResult(changed_tickets={ticket_id})


async def _board_tickets_for_installation(
    session: AsyncSession, installation_id: int
) -> set[uuid.UUID]:
    rows = await session.scalars(
        select(Ticket.id).join(Repo).where(Repo.installation_id == installation_id)
    )
    return set(rows)


async def _reconcile(
    session: AsyncSession, gh: GitHubClient, installation_id: int, *, enable: list[str]
) -> DispatchResult:
    """Ask GitHub which repos the installation covers now, and match our rows to it."""
    accessible = await gh.list_installation_repos(installation_id)
    result = await service.reconcile_installation_repos(
        session, installation_id, accessible, enable=enable
    )
    changed = await _board_tickets_for_installation(session, installation_id)
    return DispatchResult(changed_tickets=changed, note=str(result))


async def handle_installation(
    payload: dict[str, Any], session: AsyncSession, gh: GitHubClient
) -> DispatchResult:
    installation = GhInstallation.model_validate(payload["installation"])
    action = payload.get("action")
    if action in ("created", "unsuspend", "new_permissions_accepted"):
        return await _reconcile(session, gh, installation.id, enable=[])
    if action == "suspend":
        # Keep rows so unsuspending restores them; GitHub stops sending events meanwhile.
        await service.set_repos_enabled(session, enabled=False, installation_id=installation.id)
        gh.auth.invalidate(installation.id)
        changed = await _board_tickets_for_installation(session, installation.id)
        return DispatchResult(changed_tickets=changed, note="installation suspended")
    if action == "deleted":
        # The app can no longer call GitHub for this installation, so retire everything.
        changed = await _board_tickets_for_installation(session, installation.id)
        result = await service.retire_repos(session, installation.id)
        gh.auth.invalidate(installation.id)
        return DispatchResult(changed_tickets=changed, note=f"installation deleted: {result}")
    return DispatchResult(note=f"ignored installation.{action}")


async def handle_installation_repositories(
    payload: dict[str, Any], session: AsyncSession, gh: GitHubClient
) -> DispatchResult:
    installation = GhInstallation.model_validate(payload["installation"])
    added = [GhRepoRef.model_validate(r) for r in payload.get("repositories_added") or []]
    # The payload is not a complete diff (an "all" -> "selected" switch omits the dropped
    # repos), so reconcile against GitHub's full list instead of applying it directly.
    changed_before = await _board_tickets_for_installation(session, installation.id)
    result = await _reconcile(session, gh, installation.id, enable=[r.full_name for r in added])
    result.changed_tickets |= changed_before
    return result


HANDLERS: dict[str, Handler] = {
    "issues": handle_issues,
    "pull_request": handle_pull_request,
    "installation": handle_installation,
    "installation_repositories": handle_installation_repositories,
}


async def dispatch(
    event: str, payload: dict[str, Any], session: AsyncSession, gh: GitHubClient
) -> DispatchResult:
    handler = HANDLERS.get(event)
    if handler is None:
        return DispatchResult(note=f"ignored event {event}")
    return await handler(payload, session, gh)
