"""Shared test fixtures.

Postgres and Redis come from testcontainers (one container each per session).
Set NEXTIX_TEST_DATABASE_URL / NEXTIX_TEST_REDIS_URL to reuse running services.
All GitHub HTTP goes through respx; nothing touches the real network.
"""

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

# Set before importing nextix so cached settings never see a developer's real values.
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["NEXTIX_API_TOKEN"] = "test-api-token"
os.environ["GITHUB_APP_ID"] = "12345"
# Tests choose their own Claude credentials; never inherit a developer's real ones.
# (Set, not removed: an empty environment variable still beats a .env file, which
# pydantic-settings would otherwise read from the working directory.)
os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["NEXTIX_CLAUDE_AUTH"] = "auto"
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://nextix:nextix@localhost:5432/nextix")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import httpx
import pytest
import respx
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from nextix.config import get_settings
from nextix.github.app_auth import GitHubAppAuth
from nextix.github.client import GitHubClient

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "github"
API = "https://api.github.test"

if sys.platform == "win32":
    # psycopg's async mode can't run on the Proactor loop that Windows uses by default.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined,unused-ignore]


def load_fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


# --------------------------------------------------------------------------- postgres


def _migrate(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("NEXTIX_TEST_DATABASE_URL")
    if url:
        _migrate(url)
        yield url
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        url = pg.get_connection_url()
        _migrate(url)
        yield url


@pytest.fixture
async def session_factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE check_runs, commit_statuses, artifacts, run_events, runs, tickets, "
                "repos, webhook_deliveries CASCADE"
            )
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


# --------------------------------------------------------------------------- redis


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    url = os.environ.get("NEXTIX_TEST_REDIS_URL")
    if url:
        yield url
        return
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as rc:
        yield f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0"


# --------------------------------------------------------------------------- github


@pytest.fixture(scope="session")
def private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def respx_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=API, assert_all_called=False) as mock:
        yield mock


@pytest.fixture
async def gh(private_key_pem: str, respx_mock: respx.MockRouter) -> AsyncIterator[GitHubClient]:
    respx_mock.post(path__regex=r"/app/installations/\d+/access_tokens").respond(
        201, json={"token": "ghs_test_token", "expires_at": "2099-01-01T00:00:00Z"}
    )
    async with httpx.AsyncClient() as http:
        auth = GitHubAppAuth(
            issuer="12345",
            private_key_loader=lambda: private_key_pem,
            http=http,
            api_url=API,
            api_version="2026-03-10",
        )
        yield GitHubClient(auth, http, API)


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    async def publish(self, channel: str, event: str, data: dict[str, Any]) -> None:
        self.events.append((channel, event, data))


@pytest.fixture
def publisher() -> FakePublisher:
    return FakePublisher()


class FakeEnqueuer:
    """Stands in for Celery: records the run ids that would have been queued."""

    def __init__(self) -> None:
        self.run_ids: list[Any] = []
        self.fail = False

    def __call__(self, run_id: Any) -> None:
        if self.fail:
            raise ConnectionError("broker unreachable")
        self.run_ids.append(run_id)


@pytest.fixture
def enqueuer() -> FakeEnqueuer:
    return FakeEnqueuer()
