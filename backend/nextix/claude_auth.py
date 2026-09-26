"""Which Claude credential nexTix uses, and keeping the other one out of the way.

Two ways to pay for Claude:

- ``subscription``: the owner's Claude Pro/Max plan, via a long-lived token from
  ``claude setup-token`` (``CLAUDE_CODE_OAUTH_TOKEN``). Anthropic allows this only through
  Claude Code, so everything that uses it runs Claude Code (the Claude Agent SDK), and
  only for the owner's personal use: never for other people's requests.
- ``api_key``: a Claude Console API key (``ANTHROPIC_API_KEY``), billed per use. Required
  if nexTix is ever shared with other people.

``NEXTIX_CLAUDE_AUTH`` picks one explicitly, or ``auto`` (the default) prefers the
subscription token when it is set and falls back to the API key.
"""

import os
from collections.abc import MutableMapping
from enum import StrEnum

from nextix.config import Settings


class ClaudeAuth(StrEnum):
    SUBSCRIPTION = "subscription"
    API_KEY = "api_key"
    NONE = "none"


# Variables that make Claude Code bill somewhere other than the subscription. Claude Code
# gives an API key precedence over CLAUDE_CODE_OAUTH_TOKEN (even an empty one in some
# versions), and the Agent SDK copies the parent process environment into the Claude Code
# process without a way to remove keys, so they must be absent from our own environment.
COMPETING_CREDENTIALS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)


def resolve(settings: Settings) -> ClaudeAuth:
    """The credential in effect, given what is configured."""
    has_token = bool(settings.claude_code_oauth_token.strip())
    has_key = bool(settings.anthropic_api_key.strip())
    mode = settings.nextix_claude_auth
    if mode == "subscription":
        return ClaudeAuth.SUBSCRIPTION if has_token else ClaudeAuth.NONE
    if mode == "api_key":
        return ClaudeAuth.API_KEY if has_key else ClaudeAuth.NONE
    if has_token:
        return ClaudeAuth.SUBSCRIPTION
    if has_key:
        return ClaudeAuth.API_KEY
    return ClaudeAuth.NONE


def describe_missing(settings: Settings) -> str:
    """Why no credential is in effect, in words the owner can act on."""
    mode = settings.nextix_claude_auth
    if mode == "subscription":
        return (
            "NEXTIX_CLAUDE_AUTH is 'subscription' but CLAUDE_CODE_OAUTH_TOKEN is empty. "
            "Run `claude setup-token` and paste the token into .env."
        )
    if mode == "api_key":
        return "NEXTIX_CLAUDE_AUTH is 'api_key' but ANTHROPIC_API_KEY is empty."
    return (
        "No Claude credentials are set. Add CLAUDE_CODE_OAUTH_TOKEN (your Claude plan, "
        "from `claude setup-token`) or ANTHROPIC_API_KEY to .env."
    )


def scrub_competing_credentials(
    environ: MutableMapping[str, str] | None = None,
) -> list[str]:
    """Remove variables that would override the subscription token in Claude Code.

    Call this only in subscription mode, after settings have been read. Returns the
    names removed (never their values).
    """
    env = os.environ if environ is None else environ
    removed = [name for name in COMPETING_CREDENTIALS if name in env]
    for name in removed:
        del env[name]
    return removed


def claude_code_env(settings: Settings) -> dict[str, str]:
    """Credential variables to hand a Claude Code process in subscription mode."""
    return {"CLAUDE_CODE_OAUTH_TOKEN": settings.claude_code_oauth_token.strip()}
