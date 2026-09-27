"""Before/after screenshots on the pull request itself.

When a run changed what a page looks like, the worker stores that route's before, after,
and diff images on the repo's `nextix/screenshots` branch (an orphan branch that only
ever holds images, never merged) and comments on the PR with the images side by side, so
the change can be reviewed on GitHub, including on a phone. Routes with no visible change
are listed but not shown; a run with no visible change posts nothing.
"""

import logging
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from nextix.db.models import Artifact, Repo, Run, Ticket
from nextix.github.client import GitHubClient
from nextix.runs.artifacts import resolve_path

log = logging.getLogger(__name__)

SCREENSHOTS_BRANCH = "nextix/screenshots"
IMAGE_WIDTH = 420
MAX_ROUTES_SHOWN = 10


def route_slug(label: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", label).strip("-").lower()
    return slug or "root"


def _changed(diff: Artifact) -> bool:
    meta = diff.meta or {}
    pixels = meta.get("diff_pixels")
    pct = meta.get("diff_pct")
    return bool(pixels) if isinstance(pixels, int) else bool(pct)


def _image_url(repo: Repo, path: str) -> str:
    # A blob URL with ?raw=true renders for anyone who can see the repo, private or not.
    return f"https://github.com/{repo.full_name}/blob/{quote(SCREENSHOTS_BRANCH)}/{path}?raw=true"


def comment_body(
    repo: Repo, run: Run, stored: dict[str, dict[str, str]], unchanged: list[str], baseline: str
) -> str:
    """The PR comment: one section per changed route, before and after side by side."""
    lines = [
        f"### 🖼️ Before / after (attempt {run.attempt})",
        "",
        f"Compared with **{baseline}**.",
        "",
    ]
    for label, paths in stored.items():
        pct = paths.get("pct", "")
        lines += [
            f"#### `{label}`{pct}",
            "",
            "| Before | After |",
            "|---|---|",
            f'| <img src="{_image_url(repo, paths["before"])}" width="{IMAGE_WIDTH}"> '
            f'| <img src="{_image_url(repo, paths["after"])}" width="{IMAGE_WIDTH}"> |',
            "",
        ]
        if "diff" in paths:
            lines += [f"[Changed pixels highlighted]({_image_url(repo, paths['diff'])})", ""]
    if unchanged:
        lines += ["No visible change: " + ", ".join(f"`{u}`" for u in unchanged), ""]
    return "\n".join(lines)


async def publish_screenshots(
    gh: GitHubClient,
    repo: Repo,
    ticket: Ticket,
    run: Run,
    shots: list[Artifact],
    artifact_dir: Path,
) -> bool:
    """Store the changed routes' images and comment on the PR. True if a comment was posted.

    Never raises: this is a courtesy on top of a PR that already exists.
    """
    if ticket.pr_number is None:
        return False
    by_route: dict[str, dict[str, Artifact]] = defaultdict(dict)
    for artifact in shots:
        if artifact.kind.startswith("screenshot_") and artifact.label:
            by_route[artifact.label][artifact.kind.removeprefix("screenshot_")] = artifact
    changed = [
        label
        for label, kinds in by_route.items()
        if "diff" in kinds and "before" in kinds and "after" in kinds and _changed(kinds["diff"])
    ]
    if not changed:
        return False
    unchanged = sorted(
        label for label, kinds in by_route.items() if "diff" in kinds and label not in changed
    )
    baseline = next(
        (
            str((k["before"].meta or {}).get("baseline"))
            for k in by_route.values()
            if "before" in k and (k["before"].meta or {}).get("baseline")
        ),
        repo.default_branch,
    )

    files: dict[str, bytes] = {}
    stored: dict[str, dict[str, str]] = {}
    folder = f"issue-{ticket.issue_number}/attempt-{run.attempt}"
    for label in sorted(changed)[:MAX_ROUTES_SHOWN]:
        kinds = by_route[label]
        paths: dict[str, str] = {}
        for kind in ("before", "after", "diff"):
            source = resolve_path(artifact_dir, kinds[kind])
            if source is None:
                break
            path = f"{folder}/{route_slug(label)}-{kind}.png"
            files[path] = source.read_bytes()
            paths[kind] = path
        else:
            pct = (kinds["diff"].meta or {}).get("diff_pct")
            paths["pct"] = (
                f" · {pct:.2f}% of pixels changed" if isinstance(pct, float | int) and pct else ""
            )
            stored[label] = paths
    if not stored:
        return False
    try:
        await gh.commit_files(
            repo.installation_id,
            repo.owner,
            repo.name,
            branch=SCREENSHOTS_BRANCH,
            files=files,
            message=f"Screenshots for #{ticket.issue_number}, attempt {run.attempt}",
        )
        await gh.create_comment(
            repo.installation_id,
            repo.owner,
            repo.name,
            ticket.pr_number,
            comment_body(repo, run, stored, unchanged, baseline),
        )
    except Exception:
        log.exception("could not post screenshots on %s#%s", repo.full_name, ticket.pr_number)
        return False
    return True
