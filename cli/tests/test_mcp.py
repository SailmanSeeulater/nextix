"""`nextix mcp`: the MCP tools, driven in-process through the MCP client."""

import json
from typing import Any

import httpx
import pytest
import respx
from mcp import Client

from nextix_cli.api import NextixApi
from nextix_cli.config import CliConfig
from nextix_cli.mcp_server import build_server

BASE = "http://nextix.test"
TICKET_ID = "11111111-2222-3333-4444-555555555555"
CARD = {
    "id": TICKET_ID,
    "repo": "acme/widgets",
    "issue_number": 7,
    "title": "Add dark mode",
    "column": "in_review",
    "issue_url": "https://github.com/acme/widgets/issues/7",
    "pr_url": "https://github.com/acme/widgets/pull/8",
    "latest_run": {"attempt": 1, "status": "succeeded", "agent_id": "agent-abc123"},
    "tests_failing": False,
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def server(token: str = "t0k3n", default_repo: str = "acme/widgets") -> Any:
    cfg = CliConfig(api_url=BASE, token=token, default_repo=default_repo)
    return build_server(lambda: (NextixApi(BASE, token, httpx.Client()), cfg))


async def call(name: str, args: dict[str, Any], **kwargs: Any) -> Any:
    async with Client(server(**kwargs)) as client:
        return await client.call_tool(name, args)


def payload(result: Any) -> Any:
    assert not result.is_error, result.content
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data) if isinstance(data, dict) else data
    return json.loads(result.content[0].text)


@pytest.mark.anyio
async def test_the_three_tools_are_offered() -> None:
    async with Client(server()) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
    assert tools == {"create_ticket", "list_tickets", "get_ticket_status"}


@pytest.mark.anyio
async def test_create_ticket_files_it_as_mcp() -> None:
    with respx.mock(base_url=BASE) as api:
        route = api.post("/api/tickets").respond(
            201,
            json={
                "ticket": {**CARD, "column": "doing"},
                "issue_url": CARD["issue_url"],
                "board_url": "http://board.test/",
                "needs_input": False,
                "clarifying_question": None,
            },
        )
        result = payload(await call("create_ticket", {"prompt": "add dark mode"}))
    sent = json.loads(route.calls[0].request.content)
    assert sent == {
        "repo": "acme/widgets",
        "prompt": "add dark mode",
        "labels": [],
        "triage": True,
        "created_via": "mcp",
    }
    assert route.calls[0].request.headers["authorization"] == "Bearer t0k3n"
    assert result["ticket"] == "acme/widgets#7" and result["column"] == "doing"


@pytest.mark.anyio
async def test_list_tickets_passes_filters() -> None:
    with respx.mock(base_url=BASE) as api:
        route = api.get("/api/tickets").respond(200, json=[CARD])
        result = payload(await call("list_tickets", {"column": "in_review"}))
    assert route.calls[0].request.url.params["column"] == "in_review"
    assert [t["ticket"] for t in result] == ["acme/widgets#7"]


@pytest.mark.anyio
@pytest.mark.parametrize("ref", ["7", "#7", "acme/widgets#7", TICKET_ID])
async def test_get_ticket_status_accepts_every_kind_of_reference(ref: str) -> None:
    detail = {
        **CARD,
        "runs": [
            {
                "attempt": 2,
                "trigger": "review_feedback",
                "status": "running",
                "agent_id": "agent-def456",
                "tests": None,
                "question": None,
            }
        ],
    }
    # A ticket id is used as is; only issue references need the board lookup.
    with respx.mock(base_url=BASE, assert_all_called=False) as api:
        api.get("/api/tickets").respond(200, json=[CARD])
        api.get(f"/api/tickets/{TICKET_ID}").respond(200, json=detail)
        result = payload(await call("get_ticket_status", {"ticket": ref}))
    assert result["latest_run"]["status"] == "running"
    assert result["latest_run"]["trigger"] == "review_feedback"
    assert result["runs"] == 1


@pytest.mark.anyio
async def test_errors_are_readable_tool_errors() -> None:
    result = await call("list_tickets", {}, token="")
    assert result.is_error and "nextix login" in result.content[0].text

    with respx.mock(base_url=BASE) as api:
        api.get("/api/tickets").respond(200, json=[CARD])
        missing = await call("get_ticket_status", {"ticket": "#99"})
    assert (
        missing.is_error and "acme/widgets#99 isn't on the nexTix board" in missing.content[0].text
    )

    with respx.mock(base_url=BASE) as api:
        api.get("/api/tickets").respond(401)
        refused = await call("list_tickets", {})
    assert refused.is_error and "rejected your token" in refused.content[0].text
