"""Calling Claude to structure a request into an issue.

Two interchangeable triagers produce the same validated TriageResult:

- ClaudeTriager calls the Claude API directly with an API key (Console billing).
- SubscriptionTriager runs Claude Code through the Claude Agent SDK with the owner's
  Claude plan token. Anthropic permits subscription credentials only through Claude
  Code, so this path never touches the API directly.

Both ask for structured output matching TRIAGE_OUTPUT_SCHEMA and validate it with
TriageResult. Malformed output is retried once; a refusal, a usage limit, or a second
malformed reply raises TriageError so the caller can report it clearly.
"""

import asyncio
import json
import logging
import re
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

import anthropic
import claude_agent_sdk
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
from pydantic import ValidationError

from nextix.claude_auth import ClaudeAuth, resolve
from nextix.config import Settings
from nextix.tickets.prompts import (
    TRIAGE_OUTPUT_SCHEMA,
    TRIAGE_SYSTEM_PROMPT,
    TriageResult,
    build_user_message,
)

log = logging.getLogger(__name__)

# Server-side refusal fallback: a declined request is re-run on the model Anthropic
# recommends for that refusal category, inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_ATTEMPTS = 2
TRIAGE_MAX_TOKENS = 16_000
# Claude Code answers a structured-output request in a couple of turns (it returns the
# JSON through its StructuredOutput tool); a little headroom, never an open-ended loop.
SUBSCRIPTION_MAX_TURNS = 4
# Below the CLI's 180 s request timeout, so a slow Claude never files the issue after the
# person has already been told triage failed (and retries into a duplicate).
SUBSCRIPTION_TIMEOUT_S = 150

# Claude Code expands "@path" (at the start, after whitespace or a double quote) into a read
# of that file, even with tools=[]. A fullwidth at sign keeps the text readable but inert.
_MENTION = re.compile(r"(?<![A-Za-z0-9])@")


def defuse_mentions(text: str) -> str:
    return _MENTION.sub(FULLWIDTH_AT, text)


FULLWIDTH_AT = "\N{FULLWIDTH COMMERCIAL AT}"


_SECRET = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


class TriageError(RuntimeError):
    """Triage could not produce a usable issue."""


class Triager(Protocol):
    async def triage(
        self, *, repo: str, request: str, available_labels: list[str], file_paths: list[str]
    ) -> TriageResult: ...


def parse_triage_output(text: str) -> TriageResult:
    """Parse and validate the model's JSON. Raises TriageError when malformed."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise TriageError(f"model returned invalid JSON: {exc}") from exc
    return validate_triage_output(data)


def validate_triage_output(data: object) -> TriageResult:
    try:
        return TriageResult.model_validate(data)
    except ValidationError as exc:
        raise TriageError(f"model output failed validation: {exc.error_count()} errors") from exc


def redact(text: str) -> str:
    """Remove anything shaped like an Anthropic credential before it reaches a log or user."""
    return _SECRET.sub("sk-ant-…", text)


class _Malformed(TriageError):
    """The model answered, but not with a usable triage result. Worth one retry."""


async def _with_retries(
    attempt_once: Callable[[], Awaitable[TriageResult]],
) -> TriageResult:
    """Retry malformed output once. Other TriageErrors (refusal, limits) are not retried."""
    last_error: TriageError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await attempt_once()
        except _Malformed as exc:
            log.warning("triage attempt %d malformed: %s", attempt, exc)
            last_error = exc
    assert last_error is not None
    raise TriageError(str(last_error)) from last_error


# --------------------------------------------------------------------------- API key


class ClaudeTriager:
    """Direct Claude API calls, billed to the Console account that owns the API key."""

    def __init__(self, client: anthropic.AsyncAnthropic, model: str) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_settings(cls, settings: Settings) -> "ClaudeTriager":
        if not settings.anthropic_api_key:
            raise TriageError(
                "ANTHROPIC_API_KEY is not set. Add it to .env, or create the ticket without triage."
            )
        return cls(
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=120.0),
            settings.anthropic_model,
        )

    async def triage(
        self, *, repo: str, request: str, available_labels: list[str], file_paths: list[str]
    ) -> TriageResult:
        user = build_user_message(
            repo=repo, request=request, available_labels=available_labels, file_paths=file_paths
        )

        async def once() -> TriageResult:
            text = await self._call(user)
            try:
                return parse_triage_output(text)
            except TriageError as exc:
                raise _Malformed(str(exc)) from exc

        return await _with_retries(once)

    async def _call(self, user: str) -> str:
        try:
            response = await self._client.beta.messages.create(
                model=self._model,
                max_tokens=TRIAGE_MAX_TOKENS,
                system=TRIAGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": TRIAGE_OUTPUT_SCHEMA}},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise TriageError("Anthropic rejected ANTHROPIC_API_KEY") from exc
        except anthropic.RateLimitError as exc:
            raise TriageError("Anthropic rate limit hit; try again shortly") from exc
        except anthropic.APIStatusError as exc:
            raise TriageError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise TriageError("could not reach the Anthropic API") from exc

        if response.stop_reason == "refusal":
            raise TriageError("the model declined to triage this request")
        if response.stop_reason == "max_tokens":
            return ""  # truncated JSON; counts as malformed and is retried
        return "".join(block.text for block in response.content if block.type == "text")


# --------------------------------------------------------------------------- subscription

QueryFn = Callable[..., AsyncIterator[Any]]

_LIMIT_HINTS = ("usage limit", "session limit", "weekly limit", "hit your", "rate limit")


def _explain_failure(
    *, text: str, status: int | None, subtype: str | None, reason: str | None
) -> TriageError:
    """Turn a failed Claude Code run into a sentence the owner can act on."""
    lowered = text.lower()
    token_rejected = "invalid api key" in lowered or ("oauth" in lowered and "expired" in lowered)
    if status == 401 or token_rejected:
        return TriageError(
            "Claude rejected CLAUDE_CODE_OAUTH_TOKEN. It may have expired or been revoked; "
            "run `claude setup-token` again and update .env."
        )
    if status == 429 or any(h in lowered for h in _LIMIT_HINTS):
        detail = f" ({redact(text.strip())[:200]})" if text.strip() else ""
        return TriageError(
            "Your Claude plan's usage limit is reached, so triage is paused until it resets"
            f"{detail}. File the ticket without triage meanwhile."
        )
    if subtype == "error_max_turns" or reason == "max_turns":
        return _Malformed("Claude Code ran out of turns before returning the issue")
    summary = redact(text.strip())[:300] or f"{subtype or 'error'} ({reason or 'no reason given'})"
    return TriageError(f"Claude Code failed: {summary}")


class SubscriptionTriager:
    """Triage through Claude Code on the owner's Claude plan (personal use only)."""

    def __init__(self, *, token: str, model: str, query_fn: QueryFn | None = None) -> None:
        if not token.strip():
            raise TriageError(
                "CLAUDE_CODE_OAUTH_TOKEN is empty. Run `claude setup-token` and add it to .env."
            )
        self._token = token.strip()
        self._model = model
        self._query = query_fn or claude_agent_sdk.query

    @classmethod
    def from_settings(cls, settings: Settings) -> "SubscriptionTriager":
        return cls(token=settings.claude_code_oauth_token, model=settings.anthropic_model)

    def options(self, cwd: str) -> ClaudeAgentOptions:
        """Claude Code as a pure text-in, JSON-out call: no tools, no local settings."""
        return ClaudeAgentOptions(
            system_prompt=TRIAGE_SYSTEM_PROMPT,
            tools=[],  # no tool calls; "@path" file mentions are defused in the prompt
            setting_sources=[],  # ignore any ~/.claude settings, hooks, or CLAUDE.md
            max_turns=SUBSCRIPTION_MAX_TURNS,
            model=self._model,
            output_format={"type": "json_schema", "schema": TRIAGE_OUTPUT_SCHEMA},
            cwd=cwd,
            env={"CLAUDE_CODE_OAUTH_TOKEN": self._token},
        )

    async def triage(
        self, *, repo: str, request: str, available_labels: list[str], file_paths: list[str]
    ) -> TriageResult:
        user = defuse_mentions(
            build_user_message(
                repo=repo,
                request=request,
                available_labels=available_labels,
                file_paths=file_paths,
            )
        )
        try:
            async with asyncio.timeout(SUBSCRIPTION_TIMEOUT_S):
                return await _with_retries(lambda: self._once(user))
        except TimeoutError as exc:
            raise TriageError(
                f"Claude Code took longer than {SUBSCRIPTION_TIMEOUT_S} s to answer"
            ) from exc

    async def _once(self, user: str) -> TriageResult:
        # An empty scratch directory, so Claude Code never sees this server's files.
        with tempfile.TemporaryDirectory(
            prefix="nextix-triage-", ignore_cleanup_errors=True
        ) as cwd:
            result: ResultMessage | None = None
            try:
                async for message in self._query(prompt=user, options=self.options(cwd)):
                    if isinstance(message, ResultMessage):
                        result = message
            except claude_agent_sdk.CLINotFoundError as exc:
                raise TriageError(
                    "Claude Code isn't available to the server, so subscription triage can't "
                    "run. Rebuild the backend image (it bundles Claude Code) or use an API key."
                ) from exc
            except claude_agent_sdk.ResultError as exc:
                raise _explain_failure(
                    text=exc.result or " ".join(exc.errors),
                    status=exc.api_error_status,
                    subtype=exc.subtype,
                    reason=exc.terminal_reason,
                ) from exc
            except claude_agent_sdk.ProcessError as exc:
                raise _explain_failure(
                    text=exc.stderr or str(exc), status=None, subtype=None, reason=None
                ) from exc
            except claude_agent_sdk.ClaudeSDKError as exc:
                raise TriageError(f"Claude Code failed: {redact(str(exc))[:300]}") from exc
            except Exception as exc:  # e.g. the SDK's bare "Control request timeout: initialize"
                raise TriageError(f"Claude Code failed: {redact(str(exc))[:300]}") from exc

        if result is None:
            raise _Malformed("Claude Code ended without a result")
        if result.is_error:
            raise _explain_failure(
                text=result.result or " ".join(result.errors or []),
                status=result.api_error_status,
                subtype=result.subtype,
                reason=result.terminal_reason,
            )
        if result.stop_reason == "refusal":
            raise TriageError("the model declined to triage this request")
        output = result.structured_output
        if output is None and result.result:
            # Older CLIs return the JSON as text instead of a structured field.
            try:
                output = json.loads(result.result)
            except ValueError as exc:
                raise _Malformed(f"model returned invalid JSON: {exc}") from exc
        if output is None:
            raise _Malformed("Claude Code returned no structured output")
        try:
            return validate_triage_output(output)
        except TriageError as exc:
            raise _Malformed(str(exc)) from exc


# --------------------------------------------------------------------------- selection


def build_triager(settings: Settings) -> Triager | None:
    """The triager for the credential in effect, or None when none is configured."""
    mode = resolve(settings)
    if mode is ClaudeAuth.SUBSCRIPTION:
        return SubscriptionTriager.from_settings(settings)
    if mode is ClaudeAuth.API_KEY:
        return ClaudeTriager.from_settings(settings)
    return None


__all__ = [
    "ClaudeTriager",
    "SubscriptionTriager",
    "TriageError",
    "Triager",
    "build_triager",
    "parse_triage_output",
    "redact",
]
