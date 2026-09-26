"""Artifacts a run produced: test reports and screenshots (docs/phase4.md, decision 2).

The runner writes files and a manifest under /work/.nextix-out/artifacts/. After the
container exits the worker copies that directory out, and everything in it is treated as
untrusted: kinds and file types are checked, sizes are capped, names never become paths
(each file is stored as `<artifact id>.<ext>` under ARTIFACT_DIR/<run id>/), and only
known meta keys with the right types are kept.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nextix.db.models import Artifact, Run
from nextix.redact import redact

log = logging.getLogger(__name__)

SANDBOX_DIR = "/work/.nextix-out/artifacts"
MANIFEST = "manifest.json"
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024
MAX_FILES = 64
MAX_ERRORS = 20
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

TEST_REPORT = "test_report"
SCREENSHOT_KINDS = ("screenshot_before", "screenshot_after", "screenshot_diff")
KINDS = {TEST_REPORT: ".txt", **{kind: ".png" for kind in SCREENSHOT_KINDS}}
# Meta keys kept per kind, with the types they must have.
_META: dict[str, dict[str, tuple[type, ...]]] = {
    TEST_REPORT: {
        "exit_code": (int,),
        "passed": (bool,),
        "duration_s": (int, float),
        "truncated": (bool,),
    },
    **{
        kind: {"width": (int,), "height": (int,), "diff_pixels": (int,), "diff_pct": (int, float)}
        for kind in SCREENSHOT_KINDS
    },
}
CONTENT_TYPES = {".png": "image/png", ".txt": "text/plain; charset=utf-8"}


@dataclass
class Collected:
    artifacts: list[Artifact] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)  # runner errors + our rejections


def _clean_meta(kind: str, meta: Any) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    kept: dict[str, Any] = {}
    for key, types in _META[kind].items():
        value = meta.get(key)
        # bool is an int in Python; only accept it where a bool is expected.
        if isinstance(value, bool) and bool not in types:
            continue
        if isinstance(value, types):
            kept[key] = value
    return kept


def _text(value: Any, limit: int) -> str | None:
    return redact(value)[:limit] if isinstance(value, str) and value.strip() else None


def parse(
    files: dict[str, bytes],
) -> tuple[list[tuple[str, str, bytes, dict[str, Any]]], list[dict[str, Any]]]:
    """Validate the manifest against the copied files.

    Returns ([(kind, label, content, meta)], errors). Never raises on bad input.
    """
    errors: list[dict[str, Any]] = []
    raw = files.get(MANIFEST)
    if raw is None:
        return [], errors
    try:
        manifest = json.loads(raw)
    except ValueError:
        return [], [{"step": "artifacts", "label": None, "message": "manifest.json is not JSON"}]
    if not isinstance(manifest, dict):
        return [], [{"step": "artifacts", "label": None, "message": "manifest.json is malformed"}]

    for item in (manifest.get("errors") or [])[:MAX_ERRORS]:
        if isinstance(item, dict) and _text(item.get("message"), 500):
            errors.append(
                {
                    "step": _text(item.get("step"), 50) or "runner",
                    "label": _text(item.get("label"), 300),
                    "message": _text(item.get("message"), 500),
                }
            )

    accepted: list[tuple[str, str, bytes, dict[str, Any]]] = []
    total = 0
    for item in (manifest.get("artifacts") or [])[:MAX_FILES]:
        if not isinstance(item, dict):
            continue
        kind, name = item.get("kind"), item.get("file")
        label = _text(item.get("label"), 300) or ""
        problem = None
        content = files.get(name) if isinstance(name, str) else None
        if kind not in KINDS:
            problem = f"unknown artifact kind {str(kind)[:40]!r}"
        elif not isinstance(name, str) or not name.endswith(KINDS[kind]):
            problem = f"{kind} must be a {KINDS[kind]} file"
        elif content is None:
            problem = f"{name} is listed but was not produced"
        elif len(content) > MAX_FILE_BYTES:
            problem = f"{name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB"
        elif total + len(content) > MAX_TOTAL_BYTES:
            problem = "the run's artifacts exceed the 60 MB limit"
        elif KINDS[kind] == ".png" and not content.startswith(PNG_MAGIC):
            problem = f"{name} is not a PNG image"
        if problem or content is None:
            errors.append({"step": "artifacts", "label": label or None, "message": problem})
            continue
        if kind == TEST_REPORT:
            content = redact(content.decode("utf-8", errors="replace")).encode("utf-8")
        total += len(content)
        accepted.append((str(kind), label, content, _clean_meta(str(kind), item.get("meta"))))
    return accepted, errors


async def collect(
    session: AsyncSession, run: Run, files: dict[str, bytes] | None, artifact_dir: Path
) -> Collected:
    """Store the run's artifacts on disk and as rows. Returns them and any problems."""
    result = Collected()
    if not files:
        return result
    accepted, result.errors = parse(files)
    run_dir = artifact_dir / str(run.id)
    for kind, label, content, meta in accepted:
        artifact_id = uuid.uuid4()
        relative = f"{run.id}/{artifact_id}{KINDS[kind]}"
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / relative).write_bytes(content)
        except OSError:
            log.exception("could not store an artifact of run %s", run.id)
            result.errors.append(
                {"step": "artifacts", "label": label or None, "message": "could not be stored"}
            )
            continue
        row = Artifact(
            id=artifact_id, run_id=run.id, kind=kind, label=label, path=relative, meta=meta
        )
        session.add(row)
        result.artifacts.append(row)
    await session.commit()
    return result


def artifact_json(artifact: Artifact) -> dict[str, Any]:
    """The Artifact shape from docs/phase4.md."""
    return {
        "id": str(artifact.id),
        "kind": artifact.kind,
        "label": artifact.label,
        "url": f"/api/artifacts/{artifact.id}",
        "meta": artifact.meta or {},
    }


def resolve_path(artifact_dir: Path, artifact: Artifact) -> Path | None:
    """The artifact's file, only if it really is inside the artifact directory."""
    root = artifact_dir.resolve()
    path = (root / artifact.path).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None
