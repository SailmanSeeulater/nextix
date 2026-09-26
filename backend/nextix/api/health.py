"""Health endpoint: probes Postgres and Redis and reports per-dependency status."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

from nextix import __version__
from nextix.config import get_settings
from nextix.db.session import get_async_engine

router = APIRouter(tags=["health"])

Status = Literal["ok", "error"]
Probe = Callable[[], Awaitable[None]]


class HealthResponse(BaseModel):
    status: Status
    version: str
    checks: dict[str, Status]
    # Which Claude credential triage uses: subscription | api_key | none. Never the value.
    claude: str = "none"


async def probe_db() -> None:
    async with get_async_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def probe_redis() -> None:
    client: aioredis.Redis = aioredis.Redis.from_url(get_settings().redis_url)
    try:
        await client.ping()
    finally:
        await client.aclose()


def get_probes() -> dict[str, Probe]:
    """Dependency so tests can swap real probes for fakes."""
    return {"db": probe_db, "redis": probe_redis}


PROBE_TIMEOUT_S = 3.0


async def _run(probe: Probe) -> Status:
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            await probe()
    except Exception:  # any failure means "error" for this dependency
        return "error"
    return "ok"


@router.get("/health", response_model=HealthResponse)
async def health(
    request: Request,
    response: Response,
    probes: dict[str, Probe] = Depends(get_probes),  # noqa: B008
) -> HealthResponse:
    names = list(probes)
    results = await asyncio.gather(*(_run(probes[n]) for n in names))
    checks = dict(zip(names, results, strict=True))
    overall: Status = "ok" if all(s == "ok" for s in checks.values()) else "error"
    if overall != "ok":
        response.status_code = 503
    claude = getattr(request.app.state, "claude_auth", "none")
    return HealthResponse(status=overall, version=__version__, checks=checks, claude=str(claude))
