"""Which Claude credential is in effect, and keeping competing ones out of Claude Code."""

import pytest

from nextix.claude_auth import (
    COMPETING_CREDENTIALS,
    ClaudeAuth,
    claude_code_env,
    describe_missing,
    resolve,
    scrub_competing_credentials,
)
from nextix.config import Settings
from nextix.tickets.triage import ClaudeTriager, SubscriptionTriager, build_triager


def settings(**kw: str) -> Settings:
    return Settings(**kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("mode", "token", "key", "expected"),
    [
        ("auto", "tok", "key", ClaudeAuth.SUBSCRIPTION),  # the plan wins when both are set
        ("auto", "tok", "", ClaudeAuth.SUBSCRIPTION),
        ("auto", "", "key", ClaudeAuth.API_KEY),
        ("auto", "", "", ClaudeAuth.NONE),
        ("auto", "   ", "  ", ClaudeAuth.NONE),  # whitespace is not a credential
        ("subscription", "tok", "key", ClaudeAuth.SUBSCRIPTION),
        ("subscription", "", "key", ClaudeAuth.NONE),  # explicit mode never falls back
        ("api_key", "tok", "key", ClaudeAuth.API_KEY),
        ("api_key", "tok", "", ClaudeAuth.NONE),
    ],
)
def test_resolve(mode: str, token: str, key: str, expected: ClaudeAuth) -> None:
    s = settings(nextix_claude_auth=mode, claude_code_oauth_token=token, anthropic_api_key=key)
    assert resolve(s) is expected


def test_build_triager_matches_mode() -> None:
    assert isinstance(build_triager(settings(claude_code_oauth_token="tok")), SubscriptionTriager)
    assert isinstance(build_triager(settings(anthropic_api_key="key")), ClaudeTriager)
    assert build_triager(settings()) is None


def test_missing_credentials_are_explained() -> None:
    assert "claude setup-token" in describe_missing(settings(nextix_claude_auth="subscription"))
    assert "ANTHROPIC_API_KEY" in describe_missing(settings(nextix_claude_auth="api_key"))
    both = describe_missing(settings())
    assert "CLAUDE_CODE_OAUTH_TOKEN" in both and "ANTHROPIC_API_KEY" in both


def test_scrub_removes_only_competing_credentials() -> None:
    env = {
        "ANTHROPIC_API_KEY": "",  # an empty key still takes precedence in Claude Code
        "ANTHROPIC_AUTH_TOKEN": "x",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDE_CODE_OAUTH_TOKEN": "keep-me",
        "PATH": "/usr/bin",
    }
    removed = scrub_competing_credentials(env)
    assert set(removed) == {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK"}
    assert env == {"CLAUDE_CODE_OAUTH_TOKEN": "keep-me", "PATH": "/usr/bin"}
    assert scrub_competing_credentials(env) == []


def test_competing_list_covers_every_billing_override() -> None:
    assert "ANTHROPIC_API_KEY" in COMPETING_CREDENTIALS
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in COMPETING_CREDENTIALS


def test_claude_code_env_carries_only_the_trimmed_token() -> None:
    assert claude_code_env(settings(claude_code_oauth_token="  tok \n")) == {
        "CLAUDE_CODE_OAUTH_TOKEN": "tok"
    }


def test_secrets_never_appear_in_settings_repr() -> None:
    s = settings(
        claude_code_oauth_token="sk-ant-oat01-secret",
        anthropic_api_key="sk-ant-api03-secret",
        github_webhook_secret="whsec",
        nextix_api_token="apitok",
    )
    text = repr(s) + str(s)
    for secret in ("sk-ant-oat01-secret", "sk-ant-api03-secret", "whsec", "apitok"):
        assert secret not in text
