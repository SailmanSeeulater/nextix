"""`nextix mcp`: an MCP server (stdio) so Claude Code or Claude Desktop can file and track
nexTix tickets. It uses the CLI's saved login (`nextix login`), so credentials live in one
place.

Register it with Claude Code:  claude mcp add nextix -- nextix mcp
"""

import re
import uuid
from collections.abc import Callable
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from nextix_cli import config
from nextix_cli.api import ApiError, NextixApi

Column = Literal["todo", "doing", "needs_input", "failed", "in_review", "done"]
_ISSUE_REF = re.compile(r"^(?:(?P<repo>[\w.-]+/[\w.-]+))?#?(?P<number>\d+)$")

INSTRUCTIONS = """nexTix turns a one-sentence change request into a GitHub issue that a Claude
agent then works on in a sandbox, ending in a pull request. Use create_ticket to file work,
list_tickets to see the board, and get_ticket_status to follow one ticket (its column, the
latest agent run, the pull request, tests, and any question the agent asked)."""


def _card(card: dict[str, Any]) -> dict[str, Any]:
    run = card.get("latest_run") or {}
    return {
        "id": card["id"],
        "ticket": f"{card['repo']}#{card['issue_number']}",
        "title": card["title"],
        "column": card["column"],
        "issue_url": card.get("issue_url"),
        "pr_url": card.get("pr_url"),
        "latest_run": (
            {
                "attempt": run.get("attempt"),
                "status": run.get("status"),
                "agent": run.get("agent_id"),
            }
            if run
            else None
        ),
        "tests_failing": bool(card.get("tests_failing")),
    }


def build_server(api_factory: Callable[[], tuple[NextixApi, config.CliConfig]]) -> MCPServer:
    """The server, with an injectable API client (tests pass a mocked one)."""
    server = MCPServer(name="nextix", instructions=INSTRUCTIONS, log_level="WARNING")

    def connect() -> tuple[NextixApi, config.CliConfig]:
        api, cfg = api_factory()
        if not cfg.token:
            raise ToolError("Not signed in to nexTix. Run `nextix login` in a terminal first.")
        return api, cfg

    def call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except ApiError as exc:
            raise ToolError(exc.message) from exc

    def resolve(ticket: str, api: NextixApi, cfg: config.CliConfig) -> str:
        """A ticket id from an id, `#12`, `12` (default repo), or `owner/name#12`."""
        ticket = ticket.strip()
        try:
            return str(uuid.UUID(ticket))
        except ValueError:
            pass
        match = _ISSUE_REF.match(ticket)
        if not match:
            raise ToolError(f"{ticket!r} isn't a ticket id, an issue number, or owner/name#n.")
        repo = match["repo"] or cfg.default_repo
        if not repo:
            raise ToolError("Say which repo (owner/name#n), or set a default with `nextix login`.")
        number = int(match["number"])
        cards = call(lambda: api.list_tickets(repo=repo))
        for card in cards:
            if card["issue_number"] == number:
                return str(card["id"])
        raise ToolError(f"{repo}#{number} isn't on the nexTix board.")

    @server.tool()
    def create_ticket(
        prompt: str, repo: str | None = None, labels: list[str] | None = None, triage: bool = True
    ) -> dict[str, Any]:
        """File a change request as a nexTix ticket (a GitHub issue an agent then works on).

        Args:
            prompt: The change in plain words, e.g. "add a dark mode toggle to settings".
            repo: owner/name; defaults to the repo chosen at `nextix login`.
            labels: Extra GitHub labels to add.
            triage: Let Claude write it up as a structured issue first (default). If the
                request is too vague, the ticket waits in Needs Input with a question.
        """
        api, cfg = connect()
        target = repo or cfg.default_repo
        if not target:
            raise ToolError("Say which repo (owner/name), or set a default with `nextix login`.")
        created = call(
            lambda: api.create_ticket(
                repo=target, prompt=prompt, labels=labels or [], triage=triage, created_via="mcp"
            )
        )
        return {
            **_card(created["ticket"]),
            "board_url": created.get("board_url"),
            "needs_input": created.get("needs_input", False),
            "clarifying_question": created.get("clarifying_question"),
        }

    @server.tool()
    def list_tickets(repo: str | None = None, column: Column | None = None) -> list[dict[str, Any]]:
        """The nexTix board: tickets with their column, latest agent run, and PR.

        Args:
            repo: owner/name to show only one repository.
            column: todo, doing, needs_input, failed, in_review, or done.
        """
        api, _ = connect()
        return [_card(c) for c in call(lambda: api.list_tickets(repo=repo, column=column))]

    @server.tool()
    def get_ticket_status(ticket: str) -> dict[str, Any]:
        """Where one ticket stands: column, latest agent run, PR, tests, and any question.

        Args:
            ticket: A ticket id, an issue number ("12" or "#12", in the default repo), or
                "owner/name#12".
        """
        api, cfg = connect()
        detail = call(lambda: api.get_ticket(resolve(ticket, api, cfg)))
        runs = detail.get("runs") or []
        latest = runs[0] if runs else None
        return {
            **_card(detail),
            "latest_run": (
                {
                    "attempt": latest.get("attempt"),
                    "trigger": latest.get("trigger"),
                    "status": latest.get("status"),
                    "agent": latest.get("agent_id"),
                    "exit_reason": latest.get("exit_reason"),
                    "question": latest.get("question"),
                    "summary": latest.get("summary"),
                    "tests": latest.get("tests"),
                    "cost_usd": latest.get("cost_usd"),
                }
                if latest
                else None
            ),
            "runs": len(runs),
        }

    return server


def serve() -> None:
    """Run over stdio. Nothing else may write to stdout, which carries the protocol."""
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per API call otherwise

    def api_factory() -> tuple[NextixApi, config.CliConfig]:
        cfg = config.load()
        return NextixApi(cfg.api_url, cfg.token), cfg

    build_server(api_factory).run("stdio")
