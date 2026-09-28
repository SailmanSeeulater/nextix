"""Pushing an agent's branch from the worker, never from the sandbox.

The sandbox only ever has a read-only token. It hands back a git bundle of its branch; the
worker pushes exactly that branch, and only if its name is `nextix/issue-<n>`. This is how
"the agent never pushes to the default branch" is enforced: structurally, not by trust.
"""

import base64
import codecs
import contextlib
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from nextix.redact import redact

BRANCH_RE = re.compile(r"^nextix/issue-\d+$")
GIT_TIMEOUT_S = 300
# Shorter values would match by accident; none of the run's real credentials are this short.
MIN_SECRET_CHARS = 8
SCAN_CHUNK_BYTES = 1024 * 1024


class PushError(Exception):
    pass


class SecretInChanges(Exception):
    """The branch contains one of the credentials the worker gave the sandbox.

    Deliberately not a PushError: nothing was pushed, and the run fails with its own reason.
    The message names the credential, never its value.
    """


class BranchPusher(Protocol):
    def push(
        self,
        *,
        repo_full_name: str,
        token: str,
        branch: str,
        default_branch: str,
        bundle: bytes,
        secrets: Mapping[str, str] | None = None,
    ) -> None: ...


def _git(args: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    try:
        proc = subprocess.run(  # fixed argv, no shell
            ["git", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # Not re-raised as is: TimeoutExpired carries the output captured so far.
        raise PushError(f"git {args[0]} timed out after {GIT_TIMEOUT_S} s") from None
    if proc.returncode != 0:
        raise PushError(f"git {args[0]} failed: {redact(proc.stderr.strip())[:500]}")


def _ref_exists(ref: str, *, cwd: Path, env: dict[str, str]) -> bool:
    try:
        _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=cwd, env=env)
    except PushError:
        return False
    return True


def base64_forms(secret: str) -> set[str]:
    """What base64 of ``secret`` always contains, wherever it sits in the encoded data.

    Same as the runner's (agent-image/runner.py): base64 encodes 3 bytes as 4 characters,
    so the encoding depends on the secret's offset (mod 3) in the input, and the groups it
    shares with its neighbours depend on them. For each offset only the groups made of the
    secret's own bytes are kept: a substring of the padded form, the unpadded form, and of
    any larger blob it was encoded inside. The URL-safe alphabet is included too.
    """
    raw = secret.encode("utf-8")
    forms: set[str] = set()
    for shift in range(3):
        encoded = base64.b64encode(b"\x00" * shift + raw).decode("ascii")
        # Drop the first group if it holds a padding byte, and a trailing partial group.
        core = encoded[4 if shift else 0 : (shift + len(raw)) // 3 * 4]
        forms.add(core)
        forms.add(core.translate(str.maketrans("+/", "-_")))
    # Never let a short core stand in for the secret: it could match unrelated text.
    return {form for form in forms if len(form) >= MIN_SECRET_CHARS}


def scan_for_secrets(
    repo: Path, branch: str, exclude: list[str], secrets: Mapping[str, str], env: dict[str, str]
) -> None:
    """Raise SecretInChanges if the branch's new commits contain any of `secrets`, as is
    or base64-encoded.

    The sandbox runs the same check before it bundles, but the sandbox is the agent's
    trust domain: this is the one that counts. Everything the new commits add is read:
    patches as text (binary and `-diff` files too, never through a repo-configured diff or
    textconv driver), what merge commits add, and the raw commit objects (author,
    committer and extra headers as well as the message). Replace refs and grafts, which
    could show git a different history than the one pushed, are switched off. The output
    is streamed, so a large branch is never held in memory at once.
    """
    needles: dict[str, str] = {}
    for name, value in secrets.items():
        if value and len(value) >= MIN_SECRET_CHARS:
            needles[value] = name
            for form in base64_forms(value):
                needles.setdefault(form, name)
    if not needles:
        return
    overlap = max(len(v) for v in needles) - 1
    args = [
        "git",
        "log",
        "-p",
        "--text",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--format=raw",
        "--diff-merges=first-parent",
        f"refs/heads/{branch}",
        "--not",
        *exclude,
        "--",
    ]
    scan_env = {**env, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull}
    proc = subprocess.Popen(  # fixed argv, no shell
        args, cwd=repo, env=scan_env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    timer = threading.Timer(GIT_TIMEOUT_S, proc.kill)
    timer.start()
    # Incremental, so a character split across two chunks still decodes; never fails.
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    found: str | None = None
    finished = False
    try:
        assert proc.stdout is not None
        tail = ""
        while chunk := proc.stdout.read(SCAN_CHUNK_BYTES):
            # Keep the end of the previous chunk, so a value split across two still matches.
            window = tail + decoder.decode(chunk)
            found = next((name for value, name in needles.items() if value in window), None)
            if found:
                break
            tail = window[-overlap:] if overlap else ""
        else:
            finished = True
    finally:
        if not finished:
            proc.kill()  # found one (the rest doesn't matter), or reading failed
        if proc.stdout is not None:
            proc.stdout.close()
        returncode = proc.wait()
        timer.cancel()
    if found:
        raise SecretInChanges(f"the branch contains the run's {found}")
    if returncode != 0:
        # Includes the watchdog's kill: an unscanned branch is never pushed.
        raise PushError(f"could not scan the branch for credentials (git log exit {returncode})")


class GitBundlePusher:
    def __init__(self, base_url: str = "https://github.com") -> None:
        self._base = base_url.rstrip("/")

    def push(
        self,
        *,
        repo_full_name: str,
        token: str,
        branch: str,
        default_branch: str,
        bundle: bytes,
        secrets: Mapping[str, str] | None = None,
    ) -> None:
        """Push the bundle's branch. `secrets` are the values the sandbox was given (name ->
        value); a branch containing any of them raises SecretInChanges and is not pushed."""
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
            # Only what this run added: not the default branch, nor what an earlier run of
            # this ticket already pushed. Either may be missing (a branch not built on the
            # default branch, a first run), and then the scan covers more, never less.
            exclude = [
                ref
                for ref in (
                    f"refs/remotes/origin/{default_branch}",
                    f"refs/remotes/origin/{branch}",
                )
                if _ref_exists(ref, cwd=repo, env=env)
            ]
            scan_for_secrets(repo, branch, exclude, secrets or {}, env)
            # Never forced: a rerun adds commits on top of the existing branch.
            _git(
                ["push", "--quiet", "origin", f"refs/heads/{branch}:refs/heads/{branch}"],
                cwd=repo,
                env=env,
            )
