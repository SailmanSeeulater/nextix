"""GET /api/costs: totals and breakdowns by day, repo, and ticket."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
    now = datetime.now(UTC)
    widgets = Repo(owner="acme", name="widgets", installation_id=1, default_branch="main")
    gadgets = Repo(owner="acme", name="gadgets", installation_id=1, default_branch="main")
    session.add_all([widgets, gadgets])
    await session.flush()
    t1 = Ticket(repo_id=widgets.id, issue_number=1, title="Dark mode", labels=["nextix"])
    t2 = Ticket(repo_id=gadgets.id, issue_number=2, title="Fix login", labels=["nextix"])
    session.add_all([t1, t2])
    await session.flush()

    def run(ticket: Ticket, attempt: int, cost: str, days_ago: int, tokens: int) -> Run:
        return Run(
            ticket_id=ticket.id,
            attempt=attempt,
            status="succeeded",
            cost_usd=Decimal(cost),
            input_tokens=tokens,
            output_tokens=tokens // 10,
            queued_at=now - timedelta(days=days_ago),
        )

    session.add_all(
        [
            run(t1, 1, "0.40", 0, 1000),
            run(t1, 2, "0.10", 2, 500),
            run(t2, 1, "1.25", 2, 9000),
            run(t2, 2, "9.99", 40, 1),  # outside a 30-day window
        ]
    )
    await session.commit()


async def test_costs_are_totalled_and_broken_down(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await seed(session)
    r = await client.get("/api/costs?days=30", headers=AUTH)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == {
        "cost_usd": 1.75,
        "input_tokens": 10500,
        "output_tokens": 1050,
        "runs": 3,
    }
    # One entry per day, including empty ones, oldest first, ending today.
    assert len(data["by_day"]) == 31
    assert data["by_day"][-1]["date"] == datetime.now(UTC).date().isoformat()
    assert data["by_day"][-1]["cost_usd"] == 0.4
    assert data["by_day"][-3]["cost_usd"] == 1.35
    assert sum(d["runs"] for d in data["by_day"]) == 3
    # Most expensive first.
    assert [(r["repo"], r["cost_usd"]) for r in data["by_repo"]] == [
        ("acme/gadgets", 1.25),
        ("acme/widgets", 0.5),
    ]
    assert [(t["issue_number"], t["runs"]) for t in data["by_ticket"]] == [(2, 1), (1, 2)]
    assert data["estimated"] is False  # no Claude plan configured in tests


async def test_costs_need_the_token_and_a_sane_window(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/costs")).status_code == 401
    assert (await client.get("/api/costs?days=0", headers=AUTH)).status_code == 422
    empty = (await client.get("/api/costs?days=7", headers=AUTH)).json()
    assert empty["total"]["runs"] == 0 and len(empty["by_day"]) == 8
