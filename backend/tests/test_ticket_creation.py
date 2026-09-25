"""POST /api/tickets end to end: fake triager, mocked GitHub, real Postgres."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.api.deps import get_github, get_publisher, get_triager
from nextix.db.models import Repo, Ticket
from nextix.db.session import get_db
from nextix.github.client import GitHubClient
from nextix.main import create_app
from nextix.tickets.creation import choose_labels
from nextix.tickets.prompts import TriageResult
from nextix.tickets.triage import TriageError
from tests.conftest import FakePublisher

AUTH = {"Authorization": "Bearer test-api-token"}
REPO = "/repos/acme/widgets"


class FakeTriager:
    def __init__(self, result: TriageResult | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def triage(self, **kwargs: Any) -> TriageResult:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


ACTIONABLE = TriageResult(
    title="Add a dark mode toggle to settings",
    body_markdown="## Context\nUsers want it.\n## Acceptance criteria\n- [ ] toggle\n",
    labels=["ui", "invented-by-model", "nextix:needs-input"],
    actionable=True,
)
VAGUE = TriageResult(
    title="Improve the app",
    body_markdown="## Context\nUnclear.",
    labels=[],
    actionable=False,
    clarifying_question="Which part of the app should change, and what should it do?",
)


@pytest.fixture
def triager() -> FakeTriager:
    return FakeTriager(ACTIONABLE)


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    triager: FakeTriager,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_github] = lambda: gh
    app.dependency_overrides[get_publisher] = lambda: publisher
    app.dependency_overrides[get_triager] = lambda: triager
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def repo(session: AsyncSession) -> Repo:
    r = Repo(owner="acme", name="widgets", installation_id=777, default_branch="main")
    session.add(r)
    await session.commit()
    return r


def mock_github(respx_mock: respx.MockRouter, *, labels: list[str]) -> dict[str, respx.Route]:
    return {
        "labels": respx_mock.get(f"{REPO}/labels").respond(200, json=[{"name": n} for n in labels]),
        "tree": respx_mock.get(f"{REPO}/git/trees/main").respond(
            200,
            json={
                "tree": [
                    {"path": "src/settings.tsx", "type": "blob"},
                    {"path": "src", "type": "tree"},
                ]
            },
        ),
        "create_label": respx_mock.post(f"{REPO}/labels").respond(201, json={}),
        "issue": respx_mock.post(f"{REPO}/issues").mock(
            side_effect=lambda req: httpx.Response(
                201,
                json={
                    "number": 7,
                    "title": json.loads(req.content)["title"],
                    "body": json.loads(req.content)["body"],
                    "state": "open",
                    "labels": [{"name": n} for n in json.loads(req.content)["labels"]],
                },
            )
        ),
        "comment": respx_mock.post(f"{REPO}/issues/7/comments").respond(201, json={}),
    }


async def post(client: httpx.AsyncClient, **body: Any) -> httpx.Response:
    return await client.post(
        "/api/tickets",
        json={"repo": "acme/widgets", "prompt": "add dark mode", **body},
        headers=AUTH,
    )


# ------------------------------------------------------------------ happy paths


async def test_actionable_prompt_creates_structured_issue(
    client: httpx.AsyncClient,
    session: AsyncSession,
    repo: Repo,
    respx_mock: respx.MockRouter,
    triager: FakeTriager,
    publisher: FakePublisher,
) -> None:
    routes = mock_github(respx_mock, labels=["ui", "bug"])
    r = await post(client, labels=["priority"])
    assert r.status_code == 201, r.text
    data = r.json()

    issue = json.loads(routes["issue"].calls[0].request.content)
    assert issue["title"] == "Add a dark mode toggle to settings"
    assert "## Acceptance criteria" in issue["body"]
    assert "<summary>Original request</summary>" in issue["body"]
    # nextix first, then the user's label, then only model labels that already exist.
    assert issue["labels"] == ["nextix", "priority", "ui"]
    created = {json.loads(c.request.content)["name"] for c in routes["create_label"].calls}
    assert created == {"nextix", "priority"}
    assert not routes["comment"].called

    [call] = triager.calls
    assert call["file_paths"] == ["src/settings.tsx"]
    assert call["available_labels"] == ["ui", "bug"]

    assert data["needs_input"] is False
    assert data["ticket"]["column"] == "todo"
    assert data["ticket"]["created_via"] == "cli"
    assert data["issue_url"] == "https://github.com/acme/widgets/issues/7"
    assert data["board_url"].endswith("/")
    assert publisher.events[-1][1] == "ticket.updated"

    t = await session.scalar(select(Ticket))
    assert t is not None and t.issue_number == 7 and t.created_via == "cli"


async def test_vague_prompt_lands_in_needs_input_with_question(
    client: httpx.AsyncClient,
    repo: Repo,
    respx_mock: respx.MockRouter,
    triager: FakeTriager,
) -> None:
    triager.result = VAGUE
    routes = mock_github(respx_mock, labels=["nextix"])
    r = await post(client, prompt="make it better")
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["needs_input"] is True
    assert data["clarifying_question"] == VAGUE.clarifying_question
    assert data["ticket"]["column"] == "needs_input"

    issue = json.loads(routes["issue"].calls[0].request.content)
    assert issue["labels"] == ["nextix", "nextix:needs-input"]
    comment = json.loads(routes["comment"].calls[0].request.content)["body"]
    assert VAGUE.clarifying_question in comment  # type: ignore[operator]
    [label] = [json.loads(c.request.content) for c in routes["create_label"].calls]
    assert label["name"] == "nextix:needs-input" and label["color"] == "fbca04"


async def test_no_triage_uses_prompt_verbatim(
    client: httpx.AsyncClient, repo: Repo, respx_mock: respx.MockRouter, triager: FakeTriager
) -> None:
    routes = mock_github(respx_mock, labels=["nextix"])
    r = await post(client, prompt="Add CONTRIBUTING.md\n\nExplain how to run tests.", triage=False)
    assert r.status_code == 201, r.text
    issue = json.loads(routes["issue"].calls[0].request.content)
    assert issue["title"] == "Add CONTRIBUTING.md"
    assert issue["body"] == "Add CONTRIBUTING.md\n\nExplain how to run tests."
    assert triager.calls == []
    assert not routes["tree"].called


# ------------------------------------------------------------------ failures


async def test_requires_token(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/tickets", json={"repo": "acme/widgets", "prompt": "x"})
    assert r.status_code == 401


async def test_unknown_or_disabled_repo_is_404(
    client: httpx.AsyncClient, session: AsyncSession, repo: Repo
) -> None:
    assert (await post(client, repo="acme/nope")).status_code == 404
    repo.enabled = False
    session.add(repo)
    await session.commit()
    r = await post(client)
    assert r.status_code == 404
    assert "Install the GitHub App" in r.json()["detail"]


async def test_invalid_request_is_422(client: httpx.AsyncClient) -> None:
    assert (await post(client, repo="not a repo")).status_code == 422
    assert (await post(client, prompt="  ")).status_code == 422


async def test_triage_failure_creates_nothing(
    client: httpx.AsyncClient,
    session: AsyncSession,
    repo: Repo,
    respx_mock: respx.MockRouter,
    triager: FakeTriager,
) -> None:
    triager.result = TriageError("model returned invalid JSON")
    routes = mock_github(respx_mock, labels=[])
    r = await post(client)
    assert r.status_code == 502
    assert "Nothing was created" in r.json()["detail"]
    assert not routes["issue"].called and not routes["create_label"].called
    assert await session.scalar(select(Ticket)) is None


async def test_triage_without_api_key_is_503(
    session_factory: async_sessionmaker[AsyncSession], gh: GitHubClient, repo: Repo
) -> None:
    app = create_app()

    async def _db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_github] = lambda: gh
    app.dependency_overrides[get_triager] = lambda: None
    app.dependency_overrides[get_publisher] = FakePublisher
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post(
            "/api/tickets", json={"repo": "acme/widgets", "prompt": "add x"}, headers=AUTH
        )
        # A repo problem is reported ahead of the missing key.
        unknown = await c.post(
            "/api/tickets", json={"repo": "acme/nope", "prompt": "add x"}, headers=AUTH
        )
    assert r.status_code == 503
    assert "--no-triage" in r.json()["detail"]
    assert unknown.status_code == 404


# ------------------------------------------------------------------ label policy


def test_choose_labels_policy() -> None:
    labels = choose_labels(
        user_labels=["UI", "custom"],
        model_labels=["ui", "bug", "made-up", "nextix:anything"],
        available=["ui", "bug"],
        needs_input=True,
    )
    assert labels == ["nextix", "nextix:needs-input", "UI", "custom", "bug"]
