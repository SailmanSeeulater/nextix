"""Idempotency-Key on POST /api/tickets: a retry never files a second GitHub issue."""

# ruff: noqa: F811  (the creation suite's fixtures are imported by name, then taken as params)

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.db.models import Ticket, TicketCreation
from nextix.housekeeping import prune_ticket_creations
from nextix.tickets.triage import TriageError
from tests.test_ticket_creation import (  # noqa: F401  (fixtures by name)
    AUTH,
    FakeTriager,
    client,
    mock_github,
    repo,
    triager,
)

BODY = {"repo": "acme/widgets", "prompt": "add dark mode", "labels": ["ui"]}


async def post(client: httpx.AsyncClient, key: str | None, **body: object) -> httpx.Response:
    headers = dict(AUTH)
    if key is not None:
        headers["Idempotency-Key"] = key
    return await client.post("/api/tickets", json={**BODY, **body}, headers=headers)


@pytest.mark.usefixtures("repo")
async def test_the_same_key_returns_the_first_ticket_without_a_second_issue(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    routes = mock_github(respx_mock, labels=["ui"])
    first = await post(client, "k-1")
    assert first.status_code == 201, first.text
    again = await post(client, "k-1")
    assert again.status_code == 201
    assert again.json() == first.json()
    assert again.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    # One issue on GitHub, one ticket in the DB.
    assert len(routes["issue"].calls) == 1
    assert await session.scalar(select(func.count()).select_from(Ticket)) == 1
    record = await session.get(TicketCreation, "k-1")
    assert record is not None and str(record.ticket_id) == first.json()["ticket"]["id"]


@pytest.mark.usefixtures("repo")
async def test_without_a_key_every_post_files_an_issue(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    routes = mock_github(respx_mock, labels=["ui"])
    assert (await post(client, None)).status_code == 201
    assert (await post(client, None)).status_code == 201
    assert len(routes["issue"].calls) == 2


@pytest.mark.usefixtures("repo")
async def test_a_key_cannot_be_reused_for_a_different_request(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    routes = mock_github(respx_mock, labels=["ui"])
    assert (await post(client, "k-2")).status_code == 201
    other = await post(client, "k-2", prompt="something else entirely")
    assert other.status_code == 422
    assert "different request" in other.json()["detail"]
    assert len(routes["issue"].calls) == 1


@pytest.mark.usefixtures("repo")
async def test_a_key_whose_first_attempt_is_still_running_answers_409(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    routes = mock_github(respx_mock, labels=["ui"])
    # A reservation with no response yet is what a concurrent duplicate would find.
    from nextix.api.tickets import request_hash
    from nextix.tickets.schemas import CreateTicketRequest

    session.add(TicketCreation(key="k-3", request_hash=request_hash(CreateTicketRequest(**BODY))))
    await session.commit()
    busy = await post(client, "k-3")
    assert busy.status_code == 409
    assert routes["issue"].calls == []


@pytest.mark.usefixtures("repo")
async def test_a_failed_attempt_frees_its_key(
    client: httpx.AsyncClient,
    session: AsyncSession,
    respx_mock: respx.MockRouter,
    triager: FakeTriager,
) -> None:
    routes = mock_github(respx_mock, labels=["ui"])
    triager.result = TriageError("Claude is unavailable")
    failed = await post(client, "k-4")
    assert failed.status_code == 502
    assert await session.get(TicketCreation, "k-4") is None
    # The client retries with the same key once Claude is back.
    from tests.test_ticket_creation import ACTIONABLE

    triager.result = ACTIONABLE
    retry = await post(client, "k-4")
    assert retry.status_code == 201, retry.text
    assert "Idempotent-Replayed" not in retry.headers
    assert len(routes["issue"].calls) == 1


async def test_old_idempotency_records_are_pruned(session: AsyncSession) -> None:
    two_days_ago = datetime.now(UTC) - timedelta(days=2)
    session.add_all(
        [
            TicketCreation(key="old", request_hash="x", created_at=two_days_ago),
            TicketCreation(key="new", request_hash="y"),
        ]
    )
    await session.commit()
    assert await prune_ticket_creations(session) == 1
    assert [r.key for r in (await session.scalars(select(TicketCreation))).all()] == ["new"]
