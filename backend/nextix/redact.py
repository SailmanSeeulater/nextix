"""Scrub credentials out of text before it is stored, logged, published, or shown."""

import re
from typing import Any

# Anthropic keys and plan tokens; GitHub installation, user, OAuth and fine-grained tokens;
# and credentials embedded in URLs (https://x-access-token:TOKEN@github.com/...).
_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),
]
_REPLACEMENT = "[redacted]"


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(_REPLACEMENT, text)
    return text


def redact_obj(value: Any) -> Any:
    """Redact every string inside a JSON-like structure, and drop NUL characters, which
    Postgres can't store in JSONB (one would make the whole batch fail)."""
    if isinstance(value, str):
        return redact(value.replace("\x00", ""))
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    if isinstance(value, dict):
        return {
            (k.replace("\x00", "") if isinstance(k, str) else k): redact_obj(v)
            for k, v in value.items()
        }
    return value
