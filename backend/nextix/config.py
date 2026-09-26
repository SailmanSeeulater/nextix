"""Application settings, loaded from environment variables (and a local .env)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Infrastructure
    database_url: str = "postgresql+psycopg://nextix:nextix@localhost:5432/nextix"
    redis_url: str = "redis://localhost:6379/0"

    # Claude credentials (see nextix/claude_auth.py). Secrets are excluded from repr so a
    # logged or printed Settings object never shows them.
    anthropic_api_key: str = Field(default="", repr=False)
    anthropic_model: str = "claude-opus-5"
    # Long-lived token for the owner's Claude Pro/Max plan, from `claude setup-token`.
    # Used only through Claude Code, and only for the owner's personal use.
    claude_code_oauth_token: str = Field(default="", repr=False)
    # auto: the subscription token when set, else the API key.
    nextix_claude_auth: Literal["auto", "subscription", "api_key"] = "auto"

    # GitHub App
    github_app_id: str = ""
    # GitHub recommends the client ID as the JWT issuer; falls back to the app ID.
    github_app_client_id: str = ""
    github_app_private_key_path: Path | None = None
    github_webhook_secret: str = Field(default="", repr=False)
    github_bot_login: str = "nextix-bot[bot]"
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"
    github_api_version: str = "2026-03-10"

    # nexTix
    nextix_api_token: str = Field(default="change-me", repr=False)
    nextix_public_url: str = "http://localhost:3000"
    nextix_allowed_github_users: str = ""

    # Agent sandbox
    agent_image: str = "nextix-agent:latest"
    agent_default_timeout_min: int = 30
    agent_default_max_cost_usd: float = 3.0
    agent_max_turns: int = 60
    agent_allowed_tools: str = "Read,Edit,Write,Bash,Glob,Grep"
    # Sandboxes join this Docker network so they can reach the API for callbacks.
    agent_network: str = "nextix_agents"
    agent_callback_base: str = "http://api:8000"
    agent_mem_limit: str = "4g"
    agent_cpus: float = 2.0
    agent_pids_limit: int = 512
    artifact_dir: Path = Path("/data/artifacts")

    # CORS origins for the web app. Comma-separated.
    cors_origins: str = Field(default="http://localhost:3000")

    @property
    def allowed_github_users(self) -> frozenset[str]:
        return frozenset(
            u.strip() for u in self.nextix_allowed_github_users.split(",") if u.strip()
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
