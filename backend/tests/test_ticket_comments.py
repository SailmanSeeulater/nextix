"""GET /api/tickets/{id}/conversation and POST /api/tickets/{id}/comments: mocked GitHub,
real Postgres."""

import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.api.deps import get_github
from nextix.db.models import Repo, Ticket
from nextix.db.session import get_db
from nextix.github.client import GitHubClient
from nextix.main import create_app

AUTH = {"Authorization": "Bearer test-api-token"}
REPO = "/repos/acme/widgets"


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession], gh: GitHubClient
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_github] = lambda: gh
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def ticket_id(session: AsyncSession) -> uuid.UUID:
    repo = Repo(owner="acme", name="widgets", installation_id=1, default_branch="main")
    session.add(repo)
    await session.flush()
    ticket = Ticket(
        id=uuid.uuid4(),
        repo_id=repo.id,
        issue_number=7,
        title="Add dark mode",
        issue_state="open",
        labels=["nextix"],
    )
    session.add(ticket)
    await session.commit()
    return ticket.id


ISSUE = {
    "number": 7,
    "title": "Add dark mode",
    "body": "## Context\nUsers asked for it.",
    "state": "open",
    "labels": [{"name": "nextix"}],
}
COMMENTS = [
    {
        "id": 1,
        "body": "Which settings page?",
        "user": {"login": "nextix-perfect[bot]"},
        "created_at": "2026-10-30T09:00:00Z",
        "html_url": "https://github.com/acme/widgets/issues/7#issuecomment-1",
    },
    {
        "id": 2,
        "body": "The account one.",
        "user": {"login": "alice"},
        "created_at": "2026-10-30T09:05:00Z",
    },
]


async def test_conversation_has_the_issue_body_and_every_comment(
    client: httpx.AsyncClient, ticket_id: uuid.UUID, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{REPO}/issues/7").respond(200, json=ISSUE)
    respx_mock.get(f"{REPO}/issues/7/comments").respond(200, json=COMMENTS)

    resp = await client.get(f"/api/tickets/{ticket_id}/conversation", headers=AUTH)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ticket"]["issue_number"] == 7
    assert data["body"] == "## Context\nUsers asked for it."
    assert [(c["author"], c["body"]) for c in data["comments"]] == [
        ("nextix-perfect[bot]", "Which settings page?"),
        ("alice", "The account one."),
    ]
    assert data["comments"][0]["url"].endswith("#issuecomment-1")
    assert data["comments"][1]["url"] is None


async def test_a_comment_is_posted_to_the_issue(
    client: httpx.AsyncClient, ticket_id: uuid.UUID, respx_mock: respx.MockRouter
) -> None:
    url = "https://github.com/acme/widgets/issues/7#issuecomment-3"
    posted = respx_mock.post(f"{REPO}/issues/7/comments").respond(201, json={"html_url": url})

    resp = await client.post(
        f"/api/tickets/{ticket_id}/comments", json={"body": "Looks done to me."}, headers=AUTH
    )

    assert resp.status_code == 201, resp.text
    assert resp.json() == {"url": url}
    assert json.loads(posted.calls.last.request.content) == {"body": "Looks done to me."}


async def test_comments_need_a_token_a_body_and_a_real_ticket(
    client: httpx.AsyncClient, ticket_id: uuid.UUID
) -> None:
    path = f"/api/tickets/{ticket_id}/comments"
    assert (await client.post(path, json={"body": "hi"})).status_code == 401
    assert (await client.post(path, json={"body": "   "}, headers=AUTH)).status_code == 422
    missing = f"/api/tickets/{uuid.uuid4()}/comments"
    assert (await client.post(missing, json={"body": "hi"}, headers=AUTH)).status_code == 404
    gone = f"/api/tickets/{uuid.uuid4()}/conversation"
    assert (await client.get(gone, headers=AUTH)).status_code == 404


async def test_github_errors_become_502s(
    client: httpx.AsyncClient, ticket_id: uuid.UUID, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(f"{REPO}/issues/7/comments").respond(403, json={"message": "nope"})
    respx_mock.get(f"{REPO}/issues/7").respond(404, json={"message": "Not Found"})
    respx_mock.get(f"{REPO}/issues/7/comments").respond(200, json=[])

    posted = await client.post(
        f"/api/tickets/{ticket_id}/comments", json={"body": "hi"}, headers=AUTH
    )
    read = await client.get(f"/api/tickets/{ticket_id}/conversation", headers=AUTH)

    assert posted.status_code == 502
    assert "GitHub returned 403" in posted.json()["detail"]
    assert read.status_code == 404  # the issue was deleted or transferred
