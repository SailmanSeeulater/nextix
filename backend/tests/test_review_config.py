""".nextix.yml validation and artifact checking: pure logic, no services."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from nextix.config import get_settings
from nextix.db.models import Artifact
from nextix.runs.artifacts import MAX_FILE_BYTES, parse, resolve_path
from nextix.runs.config import ConfigError, NextixConfig, RunLimits, parse_config

PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 16

# ------------------------------------------------------------------ .nextix.yml


def test_the_spec_example_is_valid() -> None:
    config = parse_config(
        """
setup:
  - npm ci
test: npm test
app:
  start: npm run dev
  port: 3000
  ready_path: /
  ready_timeout_s: 90
  screenshots:
    - path: /
    - path: /settings
      viewport: { width: 1280, height: 800 }
agent:
  max_turns: 60
  timeout_min: 30
  max_cost_usd: 3.00
  allowed_tools: [Read, Edit, Write, Bash, Glob, Grep]
  extra_instructions: |
    Follow existing code style. Do not modify CI config.
"""
    )
    assert config.setup == ["npm ci"] and config.test == "npm test"
    assert config.app is not None and [s.path for s in config.app.screenshots] == ["/", "/settings"]
    assert config.sandbox_json()["app"]["screenshots"][0]["viewport"] == {
        "width": 1280,
        "height": 800,
    }


@pytest.mark.parametrize("text", ["", "# only a comment\n", "setup:\n"])
def test_an_empty_file_means_defaults(text: str) -> None:
    assert parse_config(text) == NextixConfig()


def test_a_single_setup_command_may_be_a_string() -> None:
    assert parse_config("setup: pip install -e .").setup == ["pip install -e ."]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tests: npm test", "tests: not a recognised key"),
        ("agent:\n  timeout_min: 500", "agent.timeout_min: Input should be less than or equal"),
        ("agent:\n  allowed_tools: [Read, Deploy]", "unknown tool(s) Deploy"),
        (
            "app:\n  start: x\n  port: 3000\n  screenshots:\n    - path: settings",
            "app.screenshots.0.path",
        ),
        (
            "app:\n  start: x\n  port: 3000\n  screenshots:\n    - path: /\n    - path: /",
            "each screenshot path may appear once: /",
        ),
        ("app:\n  start: x\n  port: 3000\n  screenshots: []", "app.screenshots"),
        ("- npm ci", "the top level must be a mapping"),
        ("setup: [npm ci\n", "line"),
    ],
)
def test_problems_are_explained(text: str, expected: str) -> None:
    with pytest.raises(ConfigError) as info:
        parse_config(text)
    assert expected in str(info.value)


def test_huge_files_are_refused() -> None:
    with pytest.raises(ConfigError, match="larger than"):
        parse_config("# " + "x" * 70_000)


def test_limits_come_from_the_file_then_the_environment() -> None:
    settings = get_settings()
    config = parse_config("agent:\n  timeout_min: 45\n  allowed_tools: [Read, Grep, Read]")
    limits = RunLimits.resolve(settings, config)
    assert limits.timeout_min == 45 and limits.allowed_tools == "Read,Grep"
    assert limits.max_turns == settings.agent_max_turns
    assert limits.max_cost_usd == settings.agent_default_max_cost_usd


# ------------------------------------------------------------------ artifacts


def manifest(*items: dict[str, Any], errors: Any = None) -> bytes:
    return json.dumps({"artifacts": list(items), "errors": errors or []}).encode()


def shot(kind: str, file: str, **meta: Any) -> dict[str, Any]:
    return {"kind": kind, "label": "/", "file": file, "meta": meta}


def test_good_artifacts_are_accepted_and_meta_is_cleaned() -> None:
    accepted, errors = parse(
        {
            "manifest.json": manifest(
                shot("screenshot_diff", "d.png", diff_pct=2.5, diff_pixels=10, script="x"),
                {
                    "kind": "test_report",
                    "label": "pytest",
                    "file": "r.txt",
                    "meta": {"passed": True, "exit_code": True},
                },
            ),
            "d.png": PNG,
            "r.txt": b"ok",
        }
    )
    assert errors == []
    kinds = {kind: meta for kind, _, _, meta in accepted}
    assert kinds["screenshot_diff"] == {"diff_pct": 2.5, "diff_pixels": 10}
    # A bool is not an exit code.
    assert kinds["test_report"] == {"passed": True}


@pytest.mark.parametrize(
    ("item", "files", "problem"),
    [
        (shot("screenshot_hack", "a.png"), {"a.png": PNG}, "unknown artifact kind"),
        (shot("screenshot_after", "a.svg"), {"a.svg": b"<svg/>"}, "must be a .png file"),
        (shot("screenshot_after", "a.png"), {}, "was not produced"),
        (shot("screenshot_after", "a.png"), {"a.png": b"<html>"}, "is not a PNG image"),
        (
            shot("screenshot_after", "a.png"),
            {"a.png": PNG + b"\x00" * MAX_FILE_BYTES},
            "larger than 10 MB",
        ),
        (shot("test_report", "r.png"), {"r.png": PNG}, "must be a .txt file"),
    ],
)
def test_bad_artifacts_are_refused_with_a_reason(
    item: dict[str, Any], files: dict[str, bytes], problem: str
) -> None:
    accepted, errors = parse({"manifest.json": manifest(item), **files})
    assert accepted == []
    assert problem in errors[0]["message"]


def test_the_runner_errors_are_kept_trimmed_and_redacted() -> None:
    _, errors = parse(
        {
            "manifest.json": manifest(
                errors=[
                    {"step": "app_after", "label": "/x", "message": "boom ghs_" + "a" * 40},
                    "not a dict",
                    {"message": ""},
                ]
            )
        }
    )
    assert errors == [{"step": "app_after", "label": "/x", "message": "boom [redacted]"}]


def test_no_manifest_or_a_broken_one_is_harmless() -> None:
    assert parse({}) == ([], [])
    accepted, errors = parse({"manifest.json": b"{nope"})
    assert accepted == [] and "not JSON" in errors[0]["message"]


def test_stored_paths_cannot_escape_the_artifact_directory(tmp_path: Path) -> None:
    (tmp_path / "ok.png").write_bytes(PNG)
    inside = Artifact(id=uuid.uuid4(), run_id=uuid.uuid4(), kind="x", path="ok.png")
    outside = Artifact(id=uuid.uuid4(), run_id=uuid.uuid4(), kind="x", path="../../etc/passwd")
    assert resolve_path(tmp_path, inside) == (tmp_path / "ok.png").resolve()
    assert resolve_path(tmp_path, outside) is None
