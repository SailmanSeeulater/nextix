"""Pushing an agent's branch from the worker, never from the sandbox.

The sandbox only ever has a read-only token. It hands back a git bundle of its branch; the
worker pushes exactly that branch, and only if its name is `nextix/issue-<n>`. This is how
"the agent never pushes to the default branch" is enforced: structurally, not by trust.
"""

import base64
import contextlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from nextix.redact import redact

BRANCH_RE = re.compile(r"^nextix/issue-\d+$")
GIT_TIMEOUT_S = 300


class PushError(Exception):
    pass


class BranchPusher(Protocol):
    def push(
        self, *, repo_full_name: str, token: str, branch: str, default_branch: str, bundle: bytes
    ) -> None: ...


def _git(args: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    proc = subprocess.run(  # fixed argv, no shell
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        raise PushError(f"git {args[0]} failed: {redact(proc.stderr.strip())[:500]}")


class GitBundlePusher:
    def __init__(self, base_url: str = "https://github.com") -> None:
        self._base = base_url.rstrip("/")

    def push(
        self, *, repo_full_name: str, token: str, branch: str, default_branch: str, bundle: bytes
    ) -> None:
        if not BRANCH_RE.fullmatch(branch):
            raise PushError(f"refusing to push {branch!r}: only nextix/issue-<n> branches")
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        # Auth goes in through the environment, so it's never in argv, a URL, or .git/config.
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"http.{self._base}/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
        }
        remote = f"{self._base}/{repo_full_name}.git"
        with tempfile.TemporaryDirectory(prefix="nextix-push-", ignore_cleanup_errors=True) as tmp:
            work = Path(tmp)
            (work / "branch.bundle").write_bytes(bundle)
            repo = work / "repo.git"
            _git(["init", "--bare", "--quiet", str(repo)], cwd=work, env=env)
            _git(["remote", "add", "origin", remote], cwd=repo, env=env)
            # The bundle's prerequisite commits (the base it was built on) must exist locally.
            refs = [f"+refs/heads/{default_branch}:refs/remotes/origin/{default_branch}"]
            _git(["fetch", "--quiet", "--filter=blob:none", "origin", *refs], cwd=repo, env=env)
            # On reruns the branch already exists remotely; fetch it too (ignore if absent).
            with contextlib.suppress(PushError):
                _git(
                    [
                        "fetch",
                        "--quiet",
                        "--filter=blob:none",
                        "origin",
                        f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
                    ],
                    cwd=repo,
                    env=env,
                )
            _git(
                [
                    "fetch",
                    "--quiet",
                    str(work / "branch.bundle"),
                    f"+refs/heads/{branch}:refs/heads/{branch}",
                ],
                cwd=repo,
                env=env,
            )
            # Never forced: a rerun adds commits on top of the existing branch.
            _git(
                ["push", "--quiet", "origin", f"refs/heads/{branch}:refs/heads/{branch}"],
                cwd=repo,
                env=env,
            )
