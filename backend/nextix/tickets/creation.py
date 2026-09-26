"""Creating a ticket from a free-text prompt (spec §7.1).

prompt -> (optional) Claude triage -> GitHub issue (+ clarifying comment) -> ticket row.
GitHub stays the source of truth: the ticket row is an upsert of the created issue.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.db.models import Ticket
from nextix.github.client import GitHubClient
from nextix.tickets import service
from nextix.tickets.prompts import MAX_TITLE_CHARS, TriageResult
from nextix.tickets.triage import Triager

LABEL_COLORS = {
    service.NEXTIX_LABEL: ("5319e7", "Managed by nexTix"),
    service.NEEDS_INPUT_LABEL: ("fbca04", "nexTix is waiting for an answer"),
}
DEFAULT_LABEL_COLOR = "ededed"


class TicketCreationError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class CreatedTicket:
    ticket_id: uuid.UUID
    issue_number: int
    needs_input: bool
    clarifying_question: str | None


def untriaged(prompt: str) -> TriageResult:
    """Issue straight from the prompt: first line as title, full prompt as body."""
    first = next((line for line in prompt.splitlines() if line.strip()), prompt)
    return TriageResult(title=first[:MAX_TITLE_CHARS], body_markdown=prompt, actionable=True)


def choose_labels(
    *, user_labels: list[str], model_labels: list[str], available: list[str], needs_input: bool
) -> list[str]:
    """nexTix labels, then the user's, then model labels that already exist in the repo.

    The model may only pick existing labels, so it can't litter the repo with new ones,
    and it can never set nexTix's own control labels.
    """
    available_lower = {a.lower() for a in available}
    ordered = [service.NEXTIX_LABEL]
    if needs_input:
        ordered.append(service.NEEDS_INPUT_LABEL)
    ordered += user_labels
    ordered += [
        m
        for m in model_labels
        if m.lower() in available_lower and not m.lower().startswith(service.NEXTIX_LABEL)
    ]
    result: list[str] = []
    for label in ordered:
        if label.lower() not in {r.lower() for r in result}:
            result.append(label)
    return result


def issue_body(result: TriageResult, prompt: str, *, triaged: bool) -> str:
    if not triaged:
        return prompt
    quoted = "\n".join(f"> {line}" if line else ">" for line in prompt.splitlines())
    return (
        f"{result.body_markdown.rstrip()}\n\n"
        f"<details><summary>Original request</summary>\n\n{quoted}\n\n</details>\n"
    )


def clarifying_comment(question: str) -> str:
    return (
        "🤖 **nexTix needs more information before starting.**\n\n"
        f"{question}\n\n"
        "Reply to this issue with the answer."
    )


async def create_ticket(
    session: AsyncSession,
    gh: GitHubClient,
    triager: Triager | None,
    *,
    triage: bool,
    repo_full_name: str,
    prompt: str,
    labels: list[str],
    created_via: str,
) -> CreatedTicket:
    owner, _, name = repo_full_name.partition("/")
    repo = await service.get_repo(session, owner, name) if owner and name else None
    if repo is None or not repo.enabled:
        raise TicketCreationError(
            404,
            f"repo {repo_full_name!r} is not connected to nexTix. "
            "Install the GitHub App on it (or run the sync command).",
        )
    if triage and triager is None:
        raise TicketCreationError(
            503,
            "Triage needs Claude credentials on the server: CLAUDE_CODE_OAUTH_TOKEN (your "
            "Claude plan, from `claude setup-token`) or ANTHROPIC_API_KEY. Set one in .env, "
            "or create the ticket without triage (--no-triage).",
        )
    inst = repo.installation_id
    available = await gh.list_labels(inst, repo.owner, repo.name)

    if triage and triager is not None:
        paths = await gh.list_file_paths(inst, repo.owner, repo.name, repo.default_branch)
        result = await triager.triage(
            repo=repo.full_name, request=prompt, available_labels=available, file_paths=paths
        )
    else:
        result = untriaged(prompt)

    needs_input = not result.actionable
    chosen = choose_labels(
        user_labels=labels,
        model_labels=result.labels,
        available=available,
        needs_input=needs_input,
    )
    available_lower = {a.lower() for a in available}
    for label in chosen:
        if label.lower() not in available_lower:
            color, description = LABEL_COLORS.get(label, (DEFAULT_LABEL_COLOR, ""))
            await gh.create_label(
                inst, repo.owner, repo.name, label=label, color=color, description=description
            )

    issue = await gh.create_issue(
        inst,
        repo.owner,
        repo.name,
        title=result.title,
        body=issue_body(result, prompt, triaged=triage),
        labels=chosen,
    )
    if needs_input and result.clarifying_question:
        await gh.create_comment(
            inst,
            repo.owner,
            repo.name,
            issue.number,
            clarifying_comment(result.clarifying_question),
        )

    ticket_id = await service.upsert_ticket_from_issue(
        session, repo, issue, created_via=created_via
    )
    # The issues.opened webhook can land first and record created_via="github".
    await session.execute(
        update(Ticket).where(Ticket.id == ticket_id).values(created_via=created_via)
    )
    return CreatedTicket(
        ticket_id=ticket_id,
        issue_number=issue.number,
        needs_input=needs_input,
        clarifying_question=result.clarifying_question,
    )
