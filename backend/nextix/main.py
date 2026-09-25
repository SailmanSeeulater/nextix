"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nextix import __version__
from nextix.api import health, stream, tickets, webhooks
from nextix.config import get_settings
from nextix.db.session import get_async_engine
from nextix.github.app_auth import GitHubAppAuth
from nextix.github.client import GitHubClient

GITHUB_HTTP_TIMEOUT_S = 20.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    http = httpx.AsyncClient(timeout=GITHUB_HTTP_TIMEOUT_S)
    app.state.github = GitHubClient(
        GitHubAppAuth.from_settings(settings, http), http, settings.github_api_url
    )
    app.state.redis = aioredis.Redis.from_url(settings.redis_url)
    try:
        yield
    finally:
        await http.aclose()
        await app.state.redis.aclose()
        await get_async_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="nexTix API", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in (health.router, tickets.router, stream.router, webhooks.router):
        app.include_router(router, prefix="/api")
    return app


app = create_app()
