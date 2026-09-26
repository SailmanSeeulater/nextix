"""GitHub App authentication: app JWTs and cached installation tokens.

Docs: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app
- JWT: RS256, ``iat`` 60s in the past for clock drift, ``exp`` at most 10 minutes out,
  ``iss`` = client ID (recommended) or app ID.
- Installation tokens last one hour. We cache them and refresh ahead of expiry.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
import jwt

from nextix.config import Settings


class GitHubNotConfiguredError(RuntimeError):
    """An operation needs GitHub App credentials that are not configured."""


class GitHubAuthError(RuntimeError):
    """GitHub rejected our app credentials."""


@dataclass(frozen=True)
class InstallationToken:
    token: str
    expires_at: float  # unix seconds

    def __repr__(self) -> str:  # never leak the token into logs
        return f"InstallationToken(expires_at={self.expires_at})"


JWT_BACKDATE_S = 60
JWT_LIFETIME_S = 9 * 60  # stay under GitHub's 10-minute maximum
TOKEN_REFRESH_MARGIN_S = 5 * 60


class GitHubAppAuth:
    def __init__(
        self,
        *,
        issuer: str,
        private_key_loader: Callable[[], str],
        http: httpx.AsyncClient,
        api_url: str,
        api_version: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._issuer = issuer
        self._load_key = private_key_loader
        self._key: str | None = None
        self._http = http
        self._api_url = api_url.rstrip("/")
        self._api_version = api_version
        self._clock = clock
        self._tokens: dict[int, InstallationToken] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    @classmethod
    def from_settings(cls, settings: Settings, http: httpx.AsyncClient) -> "GitHubAppAuth":
        issuer = settings.github_app_client_id or settings.github_app_id
        key_path = settings.github_app_private_key_path

        def load() -> str:
            if not issuer:
                raise GitHubNotConfiguredError("GITHUB_APP_CLIENT_ID or GITHUB_APP_ID is not set")
            if key_path is None or not Path(key_path).is_file():
                raise GitHubNotConfiguredError(
                    f"GitHub App private key not found at GITHUB_APP_PRIVATE_KEY_PATH={key_path}"
                )
            return Path(key_path).read_text()

        return cls(
            issuer=issuer,
            private_key_loader=load,
            http=http,
            api_url=settings.github_api_url,
            api_version=settings.github_api_version,
        )

    @property
    def api_version(self) -> str:
        return self._api_version

    def app_jwt(self) -> str:
        if self._key is None:
            self._key = self._load_key()
        now = int(self._clock())
        claims = {"iat": now - JWT_BACKDATE_S, "exp": now + JWT_LIFETIME_S, "iss": self._issuer}
        return jwt.encode(claims, self._key, algorithm="RS256")

    async def installation_token(self, installation_id: int) -> str:
        cached = self._fresh(installation_id)
        if cached:
            return cached
        lock = self._locks.setdefault(installation_id, asyncio.Lock())
        async with lock:
            cached = self._fresh(installation_id)
            if cached:
                return cached
            minted = await self._mint(installation_id)
            self._tokens[installation_id] = minted
            return minted.token

    def invalidate(self, installation_id: int) -> None:
        self._tokens.pop(installation_id, None)

    def _fresh(self, installation_id: int) -> str | None:
        cached = self._tokens.get(installation_id)
        if cached and cached.expires_at - self._clock() > TOKEN_REFRESH_MARGIN_S:
            return cached.token
        return None

    async def scoped_token(
        self, installation_id: int, *, repository: str, permissions: dict[str, str]
    ) -> str:
        """A fresh, uncached token limited to one repo and the given permissions.

        Used for anything handed outside this process (the agent sandbox gets
        ``{"contents": "read"}`` for its clone), so it is never shared with the cache.
        """
        minted = await self._mint(
            installation_id, body={"repositories": [repository], "permissions": permissions}
        )
        return minted.token

    async def _mint(
        self, installation_id: int, body: dict[str, object] | None = None
    ) -> InstallationToken:
        resp = await self._http.post(
            f"{self._api_url}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {self.app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": self._api_version,
            },
            json=body,
        )
        if resp.status_code in (401, 403, 404):
            raise GitHubAuthError(
                f"cannot mint installation token for {installation_id}: HTTP {resp.status_code}"
            )
        resp.raise_for_status()
        data = resp.json()
        expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
        return InstallationToken(token=data["token"], expires_at=expires)
