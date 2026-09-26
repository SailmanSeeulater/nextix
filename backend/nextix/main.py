"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nextix import __version__
from nextix.api import health, internal, repos, runs, stream, tickets, webhooks
from nextix.claude_auth import ClaudeAuth, describe_missing, resolve, scrub_competing_credentials
from nextix.config import get_settings
from nextix.db.session import get_async_engine
from nextix.github.app_auth import GitHubAppAuth
from nextix.github.client import GitHubClient
from nextix.log_config import configure_logging
from nextix.tickets.triage import build_triager

GITHUB_HTTP_TIMEOUT_S = 20.0
log = logging.getLogger("nextix.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    http = httpx.AsyncClient(timeout=GITHUB_HTTP_TIMEOUT_S)
    app.state.github = GitHubClient(
        GitHubAppAuth.from_settings(settings, http), http, settings.github_api_url
    )
    app.state.redis = aioredis.Redis.from_url(settings.redis_url)
    app.state.claude_auth = resolve(settings)
    if app.state.claude_auth is ClaudeAuth.SUBSCRIPTION:
        # Claude Code would bill an API key over the plan token; keep them out of its env.
        removed = scrub_competing_credentials()
        if removed:
            log.info("subscription mode: removed %s from the environment", ", ".join(removed))
    if settings.nextix_api_token.strip() in ("", "change-me"):
        log.warning(
            "NEXTIX_API_TOKEN is unset or the example value: anyone who can reach the API, "
            "including agent sandboxes, can use it. Set a long random token in .env."
        )
    app.state.triager = build_triager(settings)
    if app.state.claude_auth is ClaudeAuth.NONE:
        log.warning("Claude triage is off: %s", describe_missing(settings))
    else:
        log.info("Claude triage uses: %s", app.state.claude_auth.value)
    try:
        yield
    finally:
        await http.aclose()
        await app.state.redis.aclose()
        await get_async_engine().dispose()


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    app = FastAPI(title="nexTix API", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in (
        health.router,
        repos.router,
        tickets.router,
        runs.router,
        stream.router,
        webhooks.router,
        internal.router,
    ):
        app.include_router(router, prefix="/api")
    return app


app = create_app()
