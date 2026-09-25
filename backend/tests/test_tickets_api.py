import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.db.models import Repo, Run, Ticket
from nextix.db.session import get_db
from nextix.main import create_app

AUTH = {"Authorization": "Bearer test-api-token"}


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def seed(session: AsyncSession) -> None:
    widgets = Repo(owner="acme", name="widgets", installation_id=1, default_branch="main")
    gadgets = Repo(owner="acme", name="gadgets", installation_id=1, default_branch="main")
    hidden = Repo(
        owner="acme", name="hidden", installation_id=1, default_branch="main", enabled=False
    )
    session.add_all([widgets, gadgets, hidden])
    await session.flush()

    def t(repo: Repo, n: int, labels: list[str], **kw: object) -> Ticket:
        return Ticket(
            id=uuid.uuid4(),
            repo_id=repo.id,
            issue_number=n,
            title=f"ticket {n}",
            issue_state="open",
            labels=labels,
            **kw,
        )

    todo = t(widgets, 1, ["nextix"])
    doing = t(widgets, 2, ["nextix"])
    review = t(gadgets, 3, ["nextix"], pr_number=30, pr_state="open")
    unlabeled = t(widgets, 4, ["bug"])
    disabled = t(hidden, 5, ["nextix"])
    session.add_all([todo, doing, review, unlabeled, disabled])
    await session.flush()
    session.add_all(
        [
            Run(ticket_id=doing.id, attempt=1, status="failed"),
            Run(
                ticket_id=doing.id,
                attempt=2,
                status="running",
                agent_id="agent-ab12",
                cost_usd=Decimal("0.4200"),
            ),
        ]
    )
    await session.commit()


async def test_requires_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/tickets")).status_code == 401
    bad = await client.get("/api/tickets", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401


async def test_board_lists_only_labeled_tickets_in_enabled_repos(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await seed(session)
    r = await client.get("/api/tickets", headers=AUTH)
    assert r.status_code == 200
    by_number = {c["issue_number"]: c for c in r.json()}
    assert set(by_number) == {1, 2, 3}
    assert by_number[1]["column"] == "todo"
    assert by_number[3]["column"] == "in_review"
    assert by_number[3]["pr_url"] == "https://github.com/acme/gadgets/pull/30"


async def test_latest_run_wins(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await seed(session)
    [card] = [
        c for c in (await client.get("/api/tickets", headers=AUTH)).json() if c["issue_number"] == 2
    ]
    assert card["column"] == "doing"
    assert card["latest_run"]["attempt"] == 2
    assert card["latest_run"]["agent_id"] == "agent-ab12"
    assert card["latest_run"]["cost_usd"] == pytest.approx(0.42)


async def test_filters(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await seed(session)
    by_repo = (await client.get("/api/tickets?repo=acme/gadgets", headers=AUTH)).json()
    assert [c["issue_number"] for c in by_repo] == [3]
    by_col = (await client.get("/api/tickets?column=doing", headers=AUTH)).json()
    assert [c["issue_number"] for c in by_col] == [2]
    bad = await client.get("/api/tickets?column=nope", headers=AUTH)
    assert bad.status_code == 422


async def test_repos_lists_enabled_only(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await seed(session)
    assert (await client.get("/api/repos")).status_code == 401
    r = await client.get("/api/repos", headers=AUTH)
    assert r.status_code == 200
    assert [x["full_name"] for x in r.json()] == ["acme/gadgets", "acme/widgets"]
