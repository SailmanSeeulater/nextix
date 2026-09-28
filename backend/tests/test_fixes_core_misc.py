"""Regressions: the example API token, webhook-delivery pruning, GitHub retries and rate
limits, diff errors when GitHub auth fails, and malformed board messages."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import nextix.api.stream as stream_api
from nextix.api.deps import get_github
from nextix.config import get_settings
from nextix.db.models import Repo, Ticket, WebhookDelivery
from nextix.db.session import get_db
from nextix.github.app_auth import GitHubAuthError
from nextix.github.client import MAX_RATE_LIMIT_WAIT_S, GitHubClient
from nextix.housekeeping import prune_webhook_deliveries
from nextix.main import create_app

REPO = "/repos/acme/widgets"


# ------------------------------------------------------------------ 3. the example token


@pytest.mark.parametrize("token", ["change-me", " change-me ", ""])
async def test_the_example_token_is_never_a_credential(token: str) -> None:
    app = create_app()
    settings = get_settings().model_copy(update={"nextix_api_token": token})
    app.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/api/costs", headers={"Authorization": f"Bearer {token.strip()}"})
        assert r.status_code == 401
        assert (await c.get("/api/costs")).status_code == 401


# ------------------------------------------------------------------ 5. housekeeping


async def test_old_webhook_deliveries_are_pruned(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    session.add_all(
        [
            WebhookDelivery(
                delivery_id="old", event="issues", received_at=now - timedelta(days=31)
            ),
            WebhookDelivery(
                delivery_id="new", event="issues", received_at=now - timedelta(days=29)
            ),
            WebhookDelivery(delivery_id="now", event="issues"),
        ]
    )
    await session.commit()

    assert await prune_webhook_deliveries(session) == 1
    left = set(await session.scalars(select(WebhookDelivery.delivery_id)))
    assert left == {"new", "now"}


def test_pruning_is_scheduled_daily() -> None:
    from nextix.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["prune-webhook-deliveries"]
    assert entry["task"] == "nextix.prune_webhook_deliveries"
    assert entry["schedule"] == 24 * 60 * 60
    assert "nextix.housekeeping" in celery_app.conf.include


# ------------------------------------------------------------------ 6. retries


@pytest.fixture
def sleeps(gh: GitHubClient) -> list[float]:
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    gh._sleep = fake_sleep
    return waited


def token_route(respx_mock: respx.MockRouter) -> respx.Route:
    [route] = [r for r in respx_mock.routes if r.pattern and "access_tokens" in repr(r.pattern)]
    return route


async def test_a_401_mints_a_new_token_and_retries_once(
    gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(REPO).mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"ok": 1})]
    )
    assert await gh._get(777, REPO) == {"ok": 1}
    assert route.call_count == 2
    assert token_route(respx_mock).call_count == 2  # the cached token was dropped


async def test_a_second_401_is_raised(gh: GitHubClient, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(REPO).respond(401)
    with pytest.raises(httpx.HTTPStatusError):
        await gh._get(777, REPO)
    assert route.call_count == 2


async def test_a_429_waits_for_retry_after(
    gh: GitHubClient, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.post(f"{REPO}/issues/1/comments").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "5"}),
            httpx.Response(201, json={}),
        ]
    )
    await gh.create_comment(777, "acme", "widgets", 1, "hi")
    assert route.call_count == 2 and sleeps == [5.0]


async def test_an_exhausted_limit_waits_for_the_reset_capped(
    gh: GitHubClient, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    reset = str(int(time.time()) + 3600)
    respx_mock.get("/installation/repositories").mock(
        side_effect=[
            httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}),
            httpx.Response(200, json={"repositories": []}),
        ]
    )
    assert await gh.list_installation_repos(777) == []
    assert sleeps == [MAX_RATE_LIMIT_WAIT_S]


async def test_a_plain_403_and_a_repeated_limit_are_not_retried_forever(
    gh: GitHubClient, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    forbidden = respx_mock.get(REPO).respond(403, json={"message": "Resource not accessible"})
    with pytest.raises(httpx.HTTPStatusError):
        await gh._get(777, REPO)
    assert forbidden.call_count == 1 and sleeps == []

    limited = respx_mock.delete(f"{REPO}/issues/1/labels/x").respond(
        429, headers={"retry-after": "1"}
    )
    with pytest.raises(httpx.HTTPStatusError):
        await gh.remove_label(777, "acme", "widgets", 1, "x")
    assert limited.call_count == 2 and sleeps == [1.0]


# ------------------------------------------------------------------ 9. diff auth errors


async def test_a_github_auth_failure_on_the_diff_is_a_502(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    gh: GitHubClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Repo(owner="acme", name="widgets", installation_id=777, default_branch="main")
    session.add(repo)
    await session.flush()
    ticket = Ticket(repo_id=repo.id, issue_number=7, title="x", labels=["nextix"], pr_number=12)
    session.add(ticket)
    await session.commit()

    async def refused(installation_id: int) -> str:
        raise GitHubAuthError(f"cannot mint installation token for {installation_id}: HTTP 401")

    monkeypatch.setattr(gh.auth, "installation_token", refused)
    app = create_app()

    async def _db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_github] = lambda: gh
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get(
            f"/api/tickets/{ticket.id}/diff", headers={"Authorization": "Bearer test-api-token"}
        )
    assert r.status_code == 502
    assert "authenticate to GitHub" in r.json()["detail"]


# ------------------------------------------------------------------ 10. malformed messages


async def test_malformed_board_messages_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[Any] = [
        {"event": "ticket.updated"},  # no data
        ["not", "an", "object"],
        {"data": {}},  # no event
        {"event": "ticket.updated", "data": {"id": "t1"}},
    ]

    @asynccontextmanager
    async def fake_subscription(client: Any, channel: str) -> AsyncIterator[Any]:
        async def messages() -> AsyncIterator[Any]:
            for m in published:
                yield m

        yield messages()

    monkeypatch.setattr(stream_api, "subscription", fake_subscription)
    events = [e async for e in stream_api.board_events(None)]  # type: ignore[arg-type]
    assert events == [
        {"event": "ready", "data": "{}"},
        {"event": "ticket.updated", "data": '{"id": "t1"}'},
    ]
