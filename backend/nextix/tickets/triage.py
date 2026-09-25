"""Calling Claude to structure a request into an issue.

Uses structured outputs so the reply is JSON matching TRIAGE_OUTPUT_SCHEMA, then
validates it with TriageResult. Malformed output is retried once; a refusal or a
second malformed reply raises TriageError so the caller can report it clearly.
"""

import json
import logging
from typing import Protocol

import anthropic
from pydantic import ValidationError

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
    try:
        return TriageResult.model_validate(data)
    except ValidationError as exc:
        raise TriageError(f"model output failed validation: {exc.error_count()} errors") from exc


class ClaudeTriager:
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
        last_error: TriageError | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            text = await self._call(user)
            try:
                return parse_triage_output(text)
            except TriageError as exc:
                log.warning("triage attempt %d malformed: %s", attempt, exc)
                last_error = exc
        assert last_error is not None
        raise last_error

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
