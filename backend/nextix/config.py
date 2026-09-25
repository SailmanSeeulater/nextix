"""Application settings, loaded from environment variables (and a local .env)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Infrastructure
    database_url: str = "postgresql+psycopg://nextix:nextix@localhost:5432/nextix"
    redis_url: str = "redis://localhost:6379/0"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # GitHub App
    github_app_id: str = ""
    # GitHub recommends the client ID as the JWT issuer; falls back to the app ID.
    github_app_client_id: str = ""
    github_app_private_key_path: Path | None = None
    github_webhook_secret: str = ""
    github_bot_login: str = "nextix-bot[bot]"
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"
    github_api_version: str = "2026-03-10"

    # nexTix
    nextix_api_token: str = "change-me"
    nextix_public_url: str = "http://localhost:3000"
    nextix_allowed_github_users: str = ""

    # Agent sandbox
    agent_image: str = "nextix-agent:latest"
    agent_default_timeout_min: int = 30
    agent_default_max_cost_usd: float = 3.0
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
