"""The `nextix` command."""

import json
import webbrowser
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from nextix_cli import config
from nextix_cli.api import ApiError, NextixApi

app = typer.Typer(
    help="Create and track nexTix tickets for coding agents.",
    no_args_is_help=True,
    add_completion=False,
)
out = Console()
err = Console(stderr=True)

COLUMN_NAMES = {
    "todo": "Todo",
    "doing": "Doing",
    "needs_input": "Needs Input",
    "failed": "Failed",
    "in_review": "In Review",
    "done": "Done",
}
COLUMN_STYLES = {
    "todo": "white",
    "doing": "cyan",
    "needs_input": "yellow",
    "failed": "red",
    "in_review": "magenta",
    "done": "green",
}


def _fail(message: str) -> typer.Exit:
    err.print(f"[red]Error:[/red] {message}")
    return typer.Exit(code=1)


def _api() -> tuple[NextixApi, config.CliConfig]:
    cfg = config.load()
    if not cfg.token:
        raise _fail("Not signed in. Run `nextix login` first.")
    return NextixApi(cfg.api_url, cfg.token), cfg


def _repo(option: str | None, cfg: config.CliConfig) -> str:
    repo = option or cfg.default_repo
    if not repo:
        raise _fail("Say which repo with --repo owner/name, or set a default with `nextix login`.")
    return repo


# ------------------------------------------------------------------ login


@app.command()
def login(
    api_url: Annotated[
        str | None, typer.Option(help="nexTix API URL, e.g. http://localhost:8000")
    ] = None,
    repo: Annotated[str | None, typer.Option(help="Default repo (owner/name)")] = None,
) -> None:
    """Save the API URL and token, and check they work."""
    current = config.load()
    url = api_url or typer.prompt("nexTix API URL", default=current.api_url)
    token = typer.prompt("API token (NEXTIX_API_TOKEN)", hide_input=True)
    default_repo = (
        repo
        if repo is not None
        else typer.prompt("Default repo, owner/name (optional)", default=current.default_repo or "")
    )
    try:
        NextixApi(url, token).check()
    except ApiError as exc:
        raise _fail(exc.message) from exc
    path = config.save(
        config.CliConfig(api_url=url.rstrip("/"), token=token, default_repo=default_repo.strip())
    )
    out.print(f"[green]Signed in.[/green] Saved to {path}")


# ------------------------------------------------------------------ new


@app.command()
def new(
    prompt: Annotated[str, typer.Argument(help="Describe the change you want.")],
    repo: Annotated[str | None, typer.Option("--repo", "-r", help="owner/name")] = None,
    label: Annotated[
        list[str] | None, typer.Option("--label", "-l", help="Extra label (repeatable)")
    ] = None,
    no_triage: Annotated[
        bool, typer.Option("--no-triage", help="Use the prompt as-is; skip Claude.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the raw API response.")] = False,
) -> None:
    """Turn a prompt into a GitHub issue and put it on the board."""
    api, cfg = _api()
    target = _repo(repo, cfg)
    try:
        if no_triage or as_json:
            result = api.create_ticket(
                repo=target, prompt=prompt, labels=label or [], triage=not no_triage
            )
        else:
            with err.status("Writing the issue with Claude…"):
                result = api.create_ticket(
                    repo=target, prompt=prompt, labels=label or [], triage=True
                )
    except ApiError as exc:
        raise _fail(exc.message) from exc

    if as_json:
        out.print_json(json.dumps(result))
        return
    ticket = result["ticket"]
    out.print(
        f"[green]✓[/green] Created [bold]{ticket['repo']}#{ticket['issue_number']}[/bold]: "
        f"{ticket['title']}"
    )
    if result["needs_input"]:
        out.print(f"[yellow]? Needs input:[/yellow] {result['clarifying_question']}")
        out.print("  Answer by commenting on the issue.")
    out.print(f"  Issue: {result['issue_url']}")
    out.print(f"  Board: {result['board_url']}")


# ------------------------------------------------------------------ ls


@app.command("ls")
def list_tickets(
    repo: Annotated[str | None, typer.Option("--repo", "-r", help="owner/name")] = None,
    column: Annotated[
        str | None,
        typer.Option("--column", "-c", help="todo, doing, needs_input, failed, in_review, done"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the raw API response.")] = False,
) -> None:
    """List tickets on the board."""
    api, cfg = _api()
    try:
        tickets = api.list_tickets(repo=repo or cfg.default_repo or None, column=column)
    except ApiError as exc:
        raise _fail(exc.message) from exc
    if as_json:
        out.print_json(json.dumps(tickets))
        return
    if not tickets:
        out.print("No tickets.")
        return
    order = list(COLUMN_NAMES)
    tickets.sort(key=lambda t: (order.index(t["column"]), t["repo"], t["issue_number"]))
    table = Table(box=None, pad_edge=False, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Status")
    table.add_column("Title", overflow="fold")
    table.add_column("Repo", style="dim")
    table.add_column("PR", justify="right")
    for t in tickets:
        style = COLUMN_STYLES.get(t["column"], "white")
        table.add_row(
            str(t["issue_number"]),
            f"[{style}]{COLUMN_NAMES.get(t['column'], t['column'])}[/{style}]",
            t["title"],
            t["repo"],
            f"#{t['pr_number']}" if t.get("pr_number") else "",
        )
    out.print(table)


# ------------------------------------------------------------------ open


@app.command("open")
def open_ticket(
    number: Annotated[int, typer.Argument(help="Issue number")],
    repo: Annotated[str | None, typer.Option("--repo", "-r", help="owner/name")] = None,
    pr: Annotated[bool, typer.Option("--pr", help="Open the linked pull request instead.")] = False,
    print_only: Annotated[
        bool, typer.Option("--print", help="Print the URL instead of opening it.")
    ] = False,
) -> None:
    """Open a ticket's issue (or its PR) in the browser."""
    api, cfg = _api()
    target = _repo(repo, cfg)
    try:
        tickets = api.list_tickets(repo=target)
    except ApiError as exc:
        raise _fail(exc.message) from exc
    ticket: dict[str, Any] | None = next((t for t in tickets if t["issue_number"] == number), None)
    if ticket is None:
        raise _fail(f"No ticket #{number} on the board for {target}.")
    url = ticket["pr_url"] if pr else ticket["issue_url"]
    if not url:
        raise _fail(f"#{number} has no linked pull request yet.")
    if print_only or not webbrowser.open(url):
        out.print(url)


if __name__ == "__main__":  # pragma: no cover
    app()


# ------------------------------------------------------------------ mcp


@app.command("mcp")
def mcp_command() -> None:
    """Run the nexTix MCP server over stdio, for Claude Code or Claude Desktop.

    Register it with:  claude mcp add nextix -- nextix mcp
    """
    from nextix_cli.mcp_server import serve

    serve()
