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

from nextix.config import get_settings
from nextix.db.models import Repo, Run, Ticket, WebhookDelivery
from nextix.github.client import GitHubClient
from nextix.github.schemas import (
    GhCheckRun,
    GhInstallation,
    GhIssue,
    GhPullRequest,
    GhRepoRef,
    GhRepository,
    GhReview,
)
from nextix.review.checks import sync_checks, tickets_for_check, upsert_check_run
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


@dataclass(frozen=True)
class StartRun:
    """A run a webhook asks for, queued once the handler's changes are committed."""

    # The task exactly as the trusted person saw it when they asked (None: the ticket now).
    task_title: str | None = None
    task_body: str | None = None
    trigger: str | None = None  # None: "initial" or "retry", from the ticket's history
    review_id: int | None = None
    review_meta: dict[str, Any] | None = None


@dataclass
class DispatchResult:
    changed_tickets: set[uuid.UUID] = field(default_factory=set)
    note: str = "ok"
    start_runs: dict[uuid.UUID, StartRun] = field(default_factory=dict)


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
    result = DispatchResult(changed_tickets={ticket_id})
    if _asks_for_an_agent(payload, repo, issue):
        result.start_runs[ticket_id] = StartRun(issue.title, issue.body or "")
        result.note = "a trusted user asked for an agent: queueing a run"
    return result


def _asks_for_an_agent(payload: dict[str, Any], repo: Repo, issue: GhIssue) -> bool:
    """A trusted person, on an open nextix issue, either labeled it `nextix` or removed
    `nextix:needs-input` (meaning "I've answered, carry on").

    Issues the app itself labels (tickets created from the CLI or web) are queued by the API
    directly, and it removes needs-input itself when a run starts; those webhooks come from
    the bot, which is not trusted here.
    """
    action = payload.get("action")
    label = (payload.get("label") or {}).get("name")
    sender = (payload.get("sender") or {}).get("login")
    started = (action == "labeled" and label == service.NEXTIX_LABEL) or (
        action == "unlabeled" and label == service.NEEDS_INPUT_LABEL
    )
    return (
        started
        and issue.state == "open"
        and service.NEXTIX_LABEL in issue.label_names
        and service.NEEDS_INPUT_LABEL not in issue.label_names
        and service.is_trusted(sender, repo, get_settings().allowed_github_users)
    )


def _trusted(login: str | None, repo: Repo) -> bool:
    """Trusted people only; the app's own bot never triggers anything."""
    settings = get_settings()
    if login and login.lower() == settings.github_bot_login.lower():
        return False
    return service.is_trusted(login, repo, settings.allowed_github_users)


async def handle_issue_comment(
    payload: dict[str, Any], session: AsyncSession, gh: GitHubClient
) -> DispatchResult:
    """A trusted person answering the agent's (or triage's) question starts a new run."""
    repo = await _repo_from_event(payload, session)
    if repo is None:
        return DispatchResult(note="repo disabled or unknown")
    issue = GhIssue.model_validate(payload["issue"])
    if payload.get("action") != "created" or issue.pull_request is not None:
        return DispatchResult(note="not a new issue comment")
    ticket = await service.get_ticket(session, repo, issue.number)
    if ticket is None:
        return DispatchResult(note="not a nextix issue")
    author = ((payload.get("comment") or {}).get("user") or {}).get("login")
    waiting = service.NEEDS_INPUT_LABEL in issue.label_names
    if not waiting or issue.state != "open" or not _trusted(author, repo):
        return DispatchResult(note="comment doesn't answer a question")
    return DispatchResult(
        changed_tickets={ticket.id},
        note=f"@{author} answered the question: queueing a run",
        start_runs={ticket.id: StartRun()},
    )


# Review states (webhooks send them in lower case) that ask for changes.
_FEEDBACK_STATES = {"changes_requested", "commented"}


async def handle_pull_request_review(
    payload: dict[str, Any], session: AsyncSession, gh: GitHubClient
) -> DispatchResult:
    """A trusted reviewer asking for changes on a nextix PR starts a review_feedback run."""
    repo = await _repo_from_event(payload, session)
    if repo is None:
        return DispatchResult(note="repo disabled or unknown")
    if payload.get("action") != "submitted":
        return DispatchResult(note=f"ignored pull_request_review.{payload.get('action')}")
    pr = GhPullRequest.model_validate(payload["pull_request"])
    review = GhReview.model_validate(payload["review"])
    number = service.issue_number_from_branch(pr.head.ref)
    if number is None or pr.head.repo is None or pr.head.repo.full_name != repo.full_name:
        return DispatchResult(note="not a nextix branch")
    ticket = await service.get_ticket(session, repo, number)
    if ticket is None:
        return DispatchResult(note="not a nextix ticket")
    author = review.user.login if review.user else None
    state = review.state.lower()
    if not _trusted(author, repo) or state not in _FEEDBACK_STATES:
        return DispatchResult(note=f"review ({state}) doesn't ask for changes")
    if pr.state != "open" or ticket.issue_state != "open":
        return DispatchResult(note="the PR or issue is closed")
    comments = 0
    if not (review.body or "").strip() or state == "commented":
        try:
            comments = len(
                await gh.list_review_comments(
                    repo.installation_id, repo.owner, repo.name, pr.number, review.id
                )
            )
        except Exception:
            log.exception("could not read the comments of review %s", review.id)
        if not (review.body or "").strip() and comments == 0:
            return DispatchResult(note="an empty review")
    latest = await session.scalar(
        select(Run).where(Run.ticket_id == ticket.id).order_by(Run.attempt.desc()).limit(1)
    )
    meta = {
        "id": review.id,
        "author": author,
        "state": state,
        "html_url": review.html_url,
        "comments": comments,
    }
    return DispatchResult(
        changed_tickets={ticket.id},
        note=f"@{author} reviewed ({state}): queueing a feedback run",
        start_runs={
            ticket.id: StartRun(
                # The task the owner approved last time, not whatever the issue says now.
                task_title=latest.task_title if latest else None,
                task_body=latest.task_body if latest else None,
                trigger="review_feedback",
                review_id=review.id,
                review_meta=meta,
            )
        },
    )


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


async def handle_check_run(
    payload: dict[str, Any], session: AsyncSession, gh: GitHubClient
) -> DispatchResult:
    repo = await _repo_from_event(payload, session)
    if repo is None:
        return DispatchResult(note="repo disabled or unknown")
    check = GhCheckRun.model_validate(payload["check_run"])
    await upsert_check_run(session, repo, check)
    changed = await tickets_for_check(session, repo, check)
    return DispatchResult(changed_tickets=changed, note=f"check {check.name}: {check.status}")


async def handle_check_suite(
    payload: dict[str, Any], session: AsyncSession, gh: GitHubClient
) -> DispatchResult:
    """A finished suite: re-read its check runs, in case single check_run events were lost."""
    repo = await _repo_from_event(payload, session)
    if repo is None:
        return DispatchResult(note="repo disabled or unknown")
    suite = payload.get("check_suite") or {}
    sha = suite.get("head_sha")
    if payload.get("action") != "completed" or not isinstance(sha, str):
        return DispatchResult(note=f"ignored check_suite.{payload.get('action')}")
    tickets = list(
        await session.scalars(
            select(Ticket).where(Ticket.repo_id == repo.id, Ticket.pr_head_sha == sha)
        )
    )
    for ticket in tickets:
        await sync_checks(session, gh, repo, ticket, force=True)
    return DispatchResult(changed_tickets={t.id for t in tickets}, note="check suite completed")


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
    "pull_request_review": handle_pull_request_review,
    "issue_comment": handle_issue_comment,
    "check_run": handle_check_run,
    "check_suite": handle_check_suite,
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
