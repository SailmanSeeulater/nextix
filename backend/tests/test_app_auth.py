import asyncio
from datetime import UTC, datetime

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization

from nextix.config import Settings
from nextix.github.app_auth import (
    TOKEN_REFRESH_MARGIN_S,
    GitHubAppAuth,
    GitHubAuthError,
    GitHubNotConfiguredError,
)

API = "https://api.github.test"
TOKEN_URL = f"{API}/app/installations/777/access_tokens"
NOW = 1_800_000_000.0


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _auth(http: httpx.AsyncClient, pem: str, clock: Clock) -> GitHubAppAuth:
    return GitHubAppAuth(
        issuer="Iv23liCLIENTID",
        private_key_loader=lambda: pem,
        http=http,
        api_url=API,
        api_version="2026-03-10",
        clock=clock,
    )


def _public_key(pem: str) -> bytes:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


async def test_app_jwt_claims(private_key_pem: str) -> None:
    async with httpx.AsyncClient() as http:
        token = _auth(http, private_key_pem, Clock(NOW)).app_jwt()
    claims = jwt.decode(
        token,
        _public_key(private_key_pem),
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["iss"] == "Iv23liCLIENTID"
    assert claims["iat"] == int(NOW) - 60  # backdated for clock drift
    assert claims["exp"] - int(NOW) <= 600  # GitHub's 10-minute max
    assert jwt.get_unverified_header(token)["alg"] == "RS256"


@respx.mock
async def test_installation_token_is_cached_until_near_expiry(private_key_pem: str) -> None:
    clock = Clock(NOW)
    route = respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(201, json={"token": "ghs_one", "expires_at": _iso(NOW + 3600)}),
            httpx.Response(201, json={"token": "ghs_two", "expires_at": _iso(NOW + 7200)}),
        ]
    )
    async with httpx.AsyncClient() as http:
        auth = _auth(http, private_key_pem, clock)
        assert await auth.installation_token(777) == "ghs_one"
        clock.now = NOW + 3600 - TOKEN_REFRESH_MARGIN_S - 1
        assert await auth.installation_token(777) == "ghs_one"
        assert route.call_count == 1

        clock.now = NOW + 3600 - TOKEN_REFRESH_MARGIN_S + 1  # inside the refresh margin
        assert await auth.installation_token(777) == "ghs_two"
        assert route.call_count == 2

    request = route.calls[0].request
    assert request.headers["Authorization"].startswith("Bearer ")
    assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"


@respx.mock
async def test_concurrent_callers_mint_once(private_key_pem: str) -> None:
    route = respx.post(TOKEN_URL).respond(
        201, json={"token": "ghs_one", "expires_at": _iso(NOW + 3600)}
    )
    async with httpx.AsyncClient() as http:
        auth = _auth(http, private_key_pem, Clock(NOW))
        tokens = await asyncio.gather(*(auth.installation_token(777) for _ in range(5)))
    assert set(tokens) == {"ghs_one"}
    assert route.call_count == 1


@respx.mock
async def test_invalidate_forces_refresh(private_key_pem: str) -> None:
    route = respx.post(TOKEN_URL).respond(
        201, json={"token": "ghs_one", "expires_at": _iso(NOW + 3600)}
    )
    async with httpx.AsyncClient() as http:
        auth = _auth(http, private_key_pem, Clock(NOW))
        await auth.installation_token(777)
        auth.invalidate(777)
        await auth.installation_token(777)
    assert route.call_count == 2


@respx.mock
async def test_rejected_credentials_raise(private_key_pem: str) -> None:
    respx.post(TOKEN_URL).respond(401, json={"message": "Bad credentials"})
    async with httpx.AsyncClient() as http:
        with pytest.raises(GitHubAuthError):
            await _auth(http, private_key_pem, Clock(NOW)).installation_token(777)


def test_token_repr_hides_secret() -> None:
    from nextix.github.app_auth import InstallationToken

    assert "ghs_secret" not in repr(InstallationToken(token="ghs_secret", expires_at=NOW))


async def test_missing_key_is_a_clear_config_error() -> None:
    settings = Settings(github_app_id="1", github_app_private_key_path=None)
    async with httpx.AsyncClient() as http:
        with pytest.raises(GitHubNotConfiguredError):
            GitHubAppAuth.from_settings(settings, http).app_jwt()


async def test_client_id_preferred_over_app_id(private_key_pem: str, tmp_path: object) -> None:
    from pathlib import Path

    key = Path(str(tmp_path)) / "app.pem"
    key.write_text(private_key_pem)
    settings = Settings(
        github_app_id="1", github_app_client_id="Iv23liXYZ", github_app_private_key_path=key
    )
    async with httpx.AsyncClient() as http:
        token = GitHubAppAuth.from_settings(settings, http).app_jwt()
    assert jwt.decode(token, options={"verify_signature": False})["iss"] == "Iv23liXYZ"
