"""Regression tests for the worker-side run fixes: size caps on what the sandbox hands
back, unexpected publishing errors, non-finite numbers, the task budget, honest exit
reasons, the worker's own secret scan, lost feedback, and Celery's soft time limit."""

import base64
import io
import json
import logging
import subprocess
import tarfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.db.models import Run, Ticket
from nextix.github.client import GitHubClient
from nextix.runs import executor, push, tasks
from nextix.runs.artifacts import _clean_meta
from nextix.runs.executor import (
    MAX_TASK_BYTES,
    _parse_result,
    clean_tests,
    execute_run,
    fit_task,
    time_out_run,
)
from nextix.runs.lifecycle import start_pending_review
from nextix.runs.push import GitBundlePusher, PushError, SecretInChanges
from nextix.runs.sandbox import DockerSandbox, FileTooLarge
from tests.conftest import FakeEnqueuer, FakePublisher
from tests.test_executor import (
    SUCCESS,
    FakePusher,
    FakeSandbox,
    comments,
    ctx,
    git,
    queued_run,
    reload,
)
from tests.test_executor import github as github

REPO = "/repos/acme/widgets"


# ------------------------------------------------------------------ 1. size caps


class _Container:
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self.chunks = chunks

    def get_archive(self, path: str) -> tuple[Iterator[bytes], dict[str, Any]]:
        return self.chunks, {}


class _Containers:
    def __init__(self, container: _Container) -> None:
        self.container = container

    def get(self, container_id: str) -> _Container:
        return self.container


class _Docker:
    def __init__(self, container: _Container) -> None:
        self.containers = _Containers(container)


def docker_sandbox(chunks: Iterator[bytes]) -> DockerSandbox:
    sandbox = DockerSandbox.__new__(DockerSandbox)  # no Docker daemon needed
    sandbox._docker = _Docker(_Container(chunks))
    return sandbox


def tar_of(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_read_file_returns_a_file_within_the_cap() -> None:
    archive = tar_of("result.json", b'{"status": "succeeded"}')
    sandbox = docker_sandbox(iter([archive]))
    assert sandbox.read_file("c1", "/x/result.json", max_bytes=1024) == b'{"status": "succeeded"}'


def test_read_file_stops_streaming_an_oversized_file() -> None:
    archive = tar_of("branch.bundle", b"x" * (1024 * 1024))
    consumed = 0

    def chunks() -> Iterator[bytes]:
        nonlocal consumed
        for i in range(0, len(archive), 16 * 1024):
            consumed += 1
            yield archive[i : i + 16 * 1024]

    with pytest.raises(FileTooLarge):
        docker_sandbox(chunks()).read_file("c1", "/x/branch.bundle", max_bytes=100 * 1024)
    # Aborted soon after the cap (plus tar slack), not after reading the whole megabyte.
    assert consumed < 20


def test_read_file_checks_the_member_size_too() -> None:
    # Within the stream slack, but the file itself is over the cap.
    archive = tar_of("branch.bundle", b"x" * 2048)
    with pytest.raises(FileTooLarge):
        docker_sandbox(iter([archive])).read_file("c1", "/x/branch.bundle", max_bytes=1024)


async def test_an_oversized_bundle_fails_the_run_without_pushing(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(executor, "MAX_BUNDLE_BYTES", 4)
    run = await queued_run(session)
    pusher = FakePusher()
    await execute_run(run.id, ctx(session_factory, gh, publisher, FakeSandbox(SUCCESS), pusher))
    run, _ = await reload(session, run)
    assert (run.status, run.exit_reason) == ("failed", "bundle_too_large")
    assert pusher.calls == [] and not github["create_pr"].called
    assert "too large to publish" in comments(github)[-1]


async def test_an_oversized_result_counts_as_unreadable(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(executor, "MAX_RESULT_BYTES", 10)
    run = await queued_run(session)
    await execute_run(run.id, ctx(session_factory, gh, publisher, FakeSandbox(SUCCESS)))
    run, _ = await reload(session, run)
    assert (run.status, run.exit_reason) == ("failed", "sandbox_error")


# ------------------------------------------------------------------ 2. unexpected errors


async def test_an_unexpected_publishing_error_fails_the_run_at_once(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("disk on fire; token ghs_" + "q" * 30)

    monkeypatch.setattr(executor, "collect_artifacts", broken)
    run = await queued_run(session)
    sandbox = FakeSandbox(SUCCESS)
    await execute_run(run.id, ctx(session_factory, gh, publisher, sandbox))
    run, _ = await reload(session, run)
    assert (run.status, run.exit_reason) == ("failed", "publish_failed")
    said = comments(github)[-1]
    assert "could not publish" in said and "ghs_qqq" not in said
    assert sandbox.removed == ["c1"]


async def test_a_publishing_error_after_a_cancel_leaves_the_run_cancelled(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = await queued_run(session)
    run_id = run.id

    async def cancelled_then_broken(*args: Any, **kwargs: Any) -> None:
        async with session_factory() as other:
            row = await other.get(Run, run_id)
            assert row is not None
            row.status = "cancelled"
            await other.commit()
        raise RuntimeError("boom")

    monkeypatch.setattr(executor, "collect_artifacts", cancelled_then_broken)
    await execute_run(run.id, ctx(session_factory, gh, publisher, FakeSandbox(SUCCESS)))
    run, _ = await reload(session, run)
    assert (run.status, run.exit_reason) == ("cancelled", None)


# ------------------------------------------------------------------ 3. non-finite numbers


def test_non_finite_numbers_from_the_sandbox_are_dropped() -> None:
    raw = b'{"status": "succeeded", "cost_usd": NaN, "input_tokens": -5, "num_turns": 3}'
    result = _parse_result(raw)
    assert result is not None
    assert (result.cost_usd, result.input_tokens, result.num_turns) == (0.0, 0, 3)
    result = _parse_result(b'{"status": "succeeded", "cost_usd": Infinity}')
    assert result is not None and result.cost_usd == 0.0
    result = _parse_result(b'{"status": "succeeded", "cost_usd": 1e12}')
    assert result is not None and result.cost_usd == executor.MAX_COST_USD

    for duration in (float("nan"), float("inf"), 10**400):
        tests = clean_tests({"command": "npm test", "passed": True, "duration_s": duration})
        assert tests is not None and tests["duration_s"] is None
    assert _clean_meta("screenshot_diff", {"diff_pct": float("nan"), "diff_pixels": 3}) == {
        "diff_pixels": 3
    }
    assert _clean_meta("test_report", {"duration_s": float("-inf"), "passed": True}) == {
        "passed": True
    }


async def test_a_nan_cost_does_not_break_the_run(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session)
    result = {
        **SUCCESS,
        "cost_usd": float("nan"),
        "tests": {"command": "pytest", "passed": True, "exit_code": 0, "duration_s": float("inf")},
    }
    await execute_run(run.id, ctx(session_factory, gh, publisher, FakeSandbox(result)))
    run, _ = await reload(session, run)
    assert run.status == "succeeded" and float(run.cost_usd) == 0.0
    assert run.tests is not None and run.tests["duration_s"] is None


# ------------------------------------------------------------------ 4. the task budget


@pytest.mark.parametrize("key", ["extra_instructions", "title"])
def test_the_task_always_fits_even_with_huge_instructions(key: str) -> None:
    task = {
        "title": "Add dark mode",
        "body": "Add a toggle.",
        "extra_instructions": "Be careful.",
        "review_comments": [{"body": "x"}],
        key: "字" * 200_000,
    }
    encoded = fit_task(task)
    assert len(encoded.encode()) <= MAX_TASK_BYTES
    assert json.loads(encoded)[key]  # shortened, not dropped


def test_a_tiny_body_is_dropped_rather_than_cut_forever() -> None:
    task = {"title": "t", "body": "b", "extra_instructions": "i" * 150_000, "review_comments": []}
    assert len(fit_task(task).encode()) <= MAX_TASK_BYTES


# ------------------------------------------------------------------ 5. honest exit reasons


async def test_a_token_failure_before_the_sandbox_is_a_github_error(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    ok = github["token"].side_effect

    def token(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if (body.get("permissions") or {}).get("contents") == "read":
            return httpx.Response(500, json={"message": "GitHub is down"})
        return ok(request)  # type: ignore[no-any-return]

    github["token"].side_effect = token
    run = await queued_run(session)
    sandbox = FakeSandbox(SUCCESS)
    await execute_run(run.id, ctx(session_factory, gh, publisher, sandbox))
    run, _ = await reload(session, run)
    assert (run.status, run.exit_reason) == ("failed", "github_error")
    assert sandbox.specs == []
    assert "from GitHub" in comments(github)[-1]


async def test_a_pr_failure_after_the_push_is_pr_failed(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    github["create_pr"].respond(422, json={"message": "Validation Failed"})
    run = await queued_run(session)
    pusher = FakePusher()
    await execute_run(run.id, ctx(session_factory, gh, publisher, FakeSandbox(SUCCESS), pusher))
    run, ticket = await reload(session, run)
    assert (run.status, run.exit_reason) == ("failed", "pr_failed")
    assert len(pusher.calls) == 1 and ticket.pr_number is None
    said = comments(github)[-1]
    assert "`nextix/issue-7` was pushed" in said and "Retry will open it" in said


# ------------------------------------------------------------------ 6. the worker's secret scan


def make_remote(tmp_path: Path) -> tuple[Path, Path, Path]:
    base = tmp_path / "gh"
    remote = base / "acme" / "widgets.git"
    remote.mkdir(parents=True)
    git("init", "--bare", "--quiet", "-b", "main", str(remote), cwd=tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    git("init", "--quiet", "-b", "main", cwd=work)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        git("config", k, v, cwd=work)
    return base, remote, work


def commit(work: Path, name: str, content: str, message: str = "change") -> None:
    (work / name).write_text(content)
    git("add", ".", cwd=work)
    git("commit", "--quiet", "-m", message, cwd=work)


SECRET = "ghs_" + "S" * 36
SECRETS = {"GITHUB_TOKEN": SECRET, "NEXTIX_CALLBACK_SECRET": "short"}


def pushed(remote: Path) -> list[str]:
    return git("for-each-ref", "--format=%(refname)", cwd=remote).splitlines()


def push_bundle(base: Path, bundle: bytes, secrets: dict[str, str] | None = SECRETS) -> None:
    GitBundlePusher(base.as_uri()).push(
        repo_full_name="acme/widgets",
        token="ghs_write",
        branch="nextix/issue-7",
        default_branch="main",
        bundle=bundle,
        secrets=secrets,
    )


def branch_bundle(work: Path, spec: str = "main..nextix/issue-7") -> bytes:
    git("bundle", "create", "--quiet", "b.bundle", spec, cwd=work)
    return (work / "b.bundle").read_bytes()


@pytest.mark.parametrize("where", ["file", "message"])
def test_the_worker_refuses_a_branch_containing_a_run_secret(tmp_path: Path, where: str) -> None:
    base, remote, work = make_remote(tmp_path)
    commit(work, "README.md", "hello\n", "base")
    git("push", "--quiet", str(remote), "main", cwd=work)
    git("checkout", "--quiet", "-b", "nextix/issue-7", cwd=work)
    commit(work, "ok.txt", "fine\n")
    if where == "file":
        commit(work, "config.js", f"const token = '{SECRET}';\n")
    else:
        commit(work, "b.txt", "b\n", message=f"use {SECRET}")
    commit(work, "later.txt", "more\n")
    with pytest.raises(SecretInChanges, match="GITHUB_TOKEN") as caught:
        push_bundle(base, branch_bundle(work))
    assert SECRET not in str(caught.value)
    assert pushed(remote) == ["refs/heads/main"]


def branch_off_main(tmp_path: Path) -> tuple[Path, Path, Path]:
    base, remote, work = make_remote(tmp_path)
    commit(work, "README.md", "hello\n", "base")
    git("push", "--quiet", str(remote), "main", cwd=work)
    git("checkout", "--quiet", "-b", "nextix/issue-7", cwd=work)
    return base, remote, work


@pytest.mark.parametrize("encode", [base64.b64encode, base64.urlsafe_b64encode])
@pytest.mark.parametrize("prefix", [b"", b"x", b"xy"])
def test_a_base64_encoded_secret_is_found_at_any_offset(
    tmp_path: Path, encode: Any, prefix: bytes
) -> None:
    base, remote, work = branch_off_main(tmp_path)
    blob = encode(prefix + b"Authorization: " + SECRET.encode() + b"!").decode().rstrip("=")
    commit(work, "fixture.txt", blob + "\n")
    with pytest.raises(SecretInChanges):
        push_bundle(base, branch_bundle(work))
    assert pushed(remote) == ["refs/heads/main"]


def test_a_secret_in_a_binary_or_no_diff_file_is_found(tmp_path: Path) -> None:
    base, remote, work = branch_off_main(tmp_path)
    (work / ".gitattributes").write_text("*.dat -diff\n")
    (work / "blob.dat").write_bytes(b"\x00\x01\xff" + SECRET.encode() + b"\x00")
    git("add", ".", cwd=work)
    git("commit", "--quiet", "-m", "data", cwd=work)
    with pytest.raises(SecretInChanges):
        push_bundle(base, branch_bundle(work))
    assert pushed(remote) == ["refs/heads/main"]


def test_a_secret_in_the_commit_author_is_found(tmp_path: Path) -> None:
    base, remote, work = branch_off_main(tmp_path)
    (work / "a.txt").write_text("a\n")
    git("add", ".", cwd=work)
    git("-c", f"user.name={SECRET}", "commit", "--quiet", "-m", "innocent", cwd=work)
    with pytest.raises(SecretInChanges):
        push_bundle(base, branch_bundle(work))
    assert pushed(remote) == ["refs/heads/main"]


def test_a_secret_added_only_by_a_merge_commit_is_found(tmp_path: Path) -> None:
    base, remote, work = branch_off_main(tmp_path)
    git("checkout", "--quiet", "-b", "side", cwd=work)
    commit(work, "side.txt", "side\n")
    git("checkout", "--quiet", "nextix/issue-7", cwd=work)
    commit(work, "mine.txt", "mine\n")
    git("merge", "--quiet", "--no-ff", "--no-commit", "side", cwd=work)
    (work / "evil.txt").write_text(f"{SECRET}\n")
    git("add", ".", cwd=work)
    git("commit", "--quiet", "-m", "Merge side", cwd=work)
    with pytest.raises(SecretInChanges):
        push_bundle(base, branch_bundle(work))
    assert pushed(remote) == ["refs/heads/main"]


def test_values_already_on_the_default_branch_or_too_short_are_ignored(tmp_path: Path) -> None:
    base, remote, work = make_remote(tmp_path)
    # Not this run's doing: it is on main already.
    commit(work, "README.md", f"hello {SECRET}\n", "base")
    git("push", "--quiet", str(remote), "main", cwd=work)
    git("checkout", "--quiet", "-b", "nextix/issue-7", cwd=work)
    commit(work, "dark.css", "short body{}\n")  # "short" is a secret below 8 chars
    push_bundle(base, branch_bundle(work))
    assert pushed(remote) == ["refs/heads/main", "refs/heads/nextix/issue-7"]


def test_a_branch_with_no_default_base_is_scanned_whole(tmp_path: Path) -> None:
    base, remote, work = make_remote(tmp_path)
    commit(work, "README.md", "hello\n", "base")
    git("push", "--quiet", str(remote), "main", cwd=work)
    git("checkout", "--quiet", "--orphan", "nextix/issue-7", cwd=work)
    git("rm", "-rf", "--quiet", ".", cwd=work)
    commit(work, "leak.txt", f"{SECRET}\n", "orphan")
    commit(work, "other.txt", "x\n")
    with pytest.raises(SecretInChanges):
        push_bundle(base, branch_bundle(work, "nextix/issue-7"))
    assert pushed(remote) == ["refs/heads/main"]


async def test_the_worker_passes_the_sandbox_secrets_and_reports_a_leak(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session)
    sandbox = FakeSandbox(SUCCESS)
    pusher = FakePusher(SecretInChanges("the branch contains the run's GITHUB_TOKEN"))
    await execute_run(run.id, ctx(session_factory, gh, publisher, sandbox, pusher))
    [call] = pusher.calls
    assert call["secrets"] == sandbox.specs[0].secrets
    assert set(call["secrets"]) >= {"GITHUB_TOKEN", "NEXTIX_CALLBACK_SECRET"}
    run, ticket = await reload(session, run)
    assert (run.status, run.exit_reason) == ("failed", "secret_in_changes")
    assert ticket.pr_number is None and not github["create_pr"].called


# ------------------------------------------------------------------ 7. git timeouts


def test_a_git_timeout_is_a_push_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def slow(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(["git", "push"], 300, output="ghs_" + "t" * 30)

    monkeypatch.setattr(push.subprocess, "run", slow)
    with pytest.raises(PushError, match="timed out") as caught:
        push._git(["push"], cwd=tmp_path, env={})
    assert "ghs_" not in str(caught.value)


# ------------------------------------------------------------------ 9. the review a run answers


async def feedback_run(session: AsyncSession) -> Run:
    run = await queued_run(session)
    run.trigger, run.review_id = "review_feedback", 77
    run.review_meta = {"id": 77, "author": "acme", "state": "changes_requested"}
    ticket = await session.get(Ticket, run.ticket_id)
    assert ticket is not None
    ticket.pr_number, ticket.pr_state = 12, "open"
    await session.commit()
    return run


REVIEW = {"id": 77, "user": {"login": "acme"}, "state": "CHANGES_REQUESTED", "body": "Fix it."}


async def test_an_unreadable_review_fails_the_run_instead_of_running_blind(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(executor, "GITHUB_RETRY_DELAY_S", 0)
    run = await feedback_run(session)
    review = respx_mock.get(f"{REPO}/pulls/12/reviews/77").respond(500, json={})
    sandbox = FakeSandbox(SUCCESS)
    await execute_run(run.id, ctx(session_factory, gh, publisher, sandbox))
    assert review.call_count == 2  # one retry
    run, _ = await reload(session, run)
    assert (run.status, run.exit_reason) == ("failed", "github_error")
    assert sandbox.specs == []


async def test_a_review_read_is_retried_once_and_blank_comments_do_not_count(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(executor, "GITHUB_RETRY_DELAY_S", 0)
    run = await feedback_run(session)
    respx_mock.get(f"{REPO}/pulls/12/reviews/77").mock(
        side_effect=[httpx.Response(502, json={}), httpx.Response(200, json=REVIEW)]
    )
    inline = [{"id": i, "path": "a.js", "line": 1, "body": " "} for i in range(20)]
    inline += [{"id": 100 + i, "path": "a.js", "line": i, "body": f"c{i}"} for i in range(50)]
    respx_mock.get(f"{REPO}/pulls/12/reviews/77/comments").respond(200, json=inline)
    github["find_pr"].respond(200, json=[json.loads(github["create_pr"].return_value.content)])
    sandbox = FakeSandbox(SUCCESS)
    await execute_run(run.id, ctx(session_factory, gh, publisher, sandbox))
    task = json.loads(sandbox.specs[0].env["NEXTIX_TASK_JSON"])
    bodies = [c["body"] for c in task["review_comments"]]
    assert bodies == ["Fix it.", *(f"c{i}" for i in range(50))]


# ------------------------------------------------------------------ 10. feedback is never lost


async def finished_ticket(session: AsyncSession) -> Ticket:
    run = await queued_run(session, status="succeeded")
    ticket = await session.get(Ticket, run.ticket_id)
    assert ticket is not None
    ticket.pr_number, ticket.pr_state = 12, "open"
    await session.commit()
    return ticket


async def test_a_pending_review_survives_a_queue_outage(
    session: AsyncSession,
    gh: GitHubClient,
    publisher: FakePublisher,
    respx_mock: respx.MockRouter,
    enqueuer: FakeEnqueuer,
) -> None:
    ticket = await finished_ticket(session)
    ticket.pending_review_id = 88
    await session.commit()
    respx_mock.get(f"{REPO}/pulls/12/reviews/88").respond(200, json={**REVIEW, "id": 88})
    respx_mock.post(f"{REPO}/issues/7/comments").respond(201, json={})
    enqueuer.fail = True
    with pytest.raises(ConnectionError):
        await start_pending_review(session, ticket.id, gh=gh, publisher=publisher, enqueue=enqueuer)
    await session.refresh(ticket)
    assert ticket.pending_review_id == 88  # the next reaper beat tries again

    enqueuer.fail = False
    run = await start_pending_review(
        session, ticket.id, gh=gh, publisher=publisher, enqueue=enqueuer
    )
    assert run is not None and run.review_id == 88 and enqueuer.run_ids == [run.id]
    await session.refresh(ticket)
    assert ticket.pending_review_id is None


async def test_a_pending_comment_survives_a_queue_outage(
    session: AsyncSession,
    gh: GitHubClient,
    publisher: FakePublisher,
    respx_mock: respx.MockRouter,
    enqueuer: FakeEnqueuer,
) -> None:
    ticket = await finished_ticket(session)
    meta = {"author": "acme", "state": "commented", "body": "Use green."}
    ticket.pending_comment = meta
    await session.commit()
    respx_mock.post(f"{REPO}/issues/7/comments").respond(201, json={})
    enqueuer.fail = True
    with pytest.raises(ConnectionError):
        await start_pending_review(session, ticket.id, gh=gh, publisher=publisher, enqueue=enqueuer)
    await session.refresh(ticket)
    assert ticket.pending_comment == meta


# ------------------------------------------------------------------ 11. Celery's soft time limit


class SlowSandbox(FakeSandbox):
    def is_running(self, container_id: str) -> bool:
        raise SoftTimeLimitExceeded()


async def test_the_soft_time_limit_times_the_run_out(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session)
    sandbox = SlowSandbox(SUCCESS)
    worker = ctx(session_factory, gh, publisher, sandbox)
    with pytest.raises(SoftTimeLimitExceeded):
        await execute_run(run.id, worker)
    assert sandbox.removed == ["c1"]  # the `finally` still ran
    run, _ = await reload(session, run)
    assert run.status == "claimed"  # not mislabelled sandbox_error

    await time_out_run(run.id, worker)
    run, _ = await reload(session, run)
    assert (run.status, run.exit_reason) == ("timed_out", "timeout")
    assert sandbox.killed_runs == [run.id]
    assert "worker's time limit" in comments(github)[-1]

    await time_out_run(run.id, worker)  # idempotent
    run, _ = await reload(session, run)
    assert run.status == "timed_out"


def test_the_task_handles_the_soft_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    timed_out: list[uuid.UUID] = []

    async def execute(run_id: uuid.UUID) -> None:
        raise SoftTimeLimitExceeded()

    async def time_out(run_id: uuid.UUID) -> None:
        timed_out.append(run_id)

    monkeypatch.setattr(tasks, "_execute", execute)
    monkeypatch.setattr(tasks, "_time_out", time_out)
    run_id = uuid.uuid4()
    tasks.execute_run_task(str(run_id))
    assert timed_out == [run_id]


# ------------------------------------------------------------------ 12. an advisory proxy


async def test_a_proxy_without_the_internal_network_is_warned_about(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = await queued_run(session)
    worker = ctx(
        session_factory,
        gh,
        publisher,
        FakeSandbox(SUCCESS),
        agent_proxy_url="http://egress:3128",
        agent_network="",
    )
    logger = logging.getLogger("nextix.runs.executor")
    # "nextix" loggers don't propagate to the root, and the migrations' logging config
    # disables loggers that already exist.
    monkeypatch.setattr(logger, "disabled", False)
    logger.addHandler(caplog.handler)
    try:
        await execute_run(run.id, worker)
    finally:
        logger.removeHandler(caplog.handler)
    # Distinct records: the handler may see one record twice if the logger propagates.
    warnings = {id(r): r.getMessage() for r in caplog.records if "advisory" in r.getMessage()}
    assert len(warnings) == 1 and str(run.id) in next(iter(warnings.values()))
