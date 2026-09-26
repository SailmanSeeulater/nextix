"""The worker side of a run: claim, sandbox, outcome, push, PR; plus the reaper and pusher.

The sandbox and the pusher are fakes; GitHub is respx; Postgres is real.
"""

import asyncio
import json
import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nextix.config import Settings, get_settings
from nextix.db.models import Repo, Run, Ticket
from nextix.github.client import GitHubClient
from nextix.runs.executor import WorkerContext, execute_run, sandbox_env, sweep_orphans
from nextix.runs.push import GitBundlePusher, PushError
from nextix.runs.reaper import reap
from nextix.runs.sandbox import BUNDLE_PATH, RESULT_PATH, SandboxSpec
from tests.conftest import FakePublisher

PLAN_TOKEN = "sk-ant-oat01-" + "p" * 40
REPO = "/repos/acme/widgets"


# ------------------------------------------------------------------ fakes


class FakeSandbox:
    def __init__(
        self,
        result: dict[str, Any] | None = None,
        *,
        bundle: bytes | None = b"BUNDLE",
        polls: int | None = 1,
        start_error: Exception | None = None,
    ) -> None:
        self.result = result
        self.bundle = bundle
        self.polls = polls  # None: runs until killed
        self.start_error = start_error
        self.specs: list[SandboxSpec] = []
        self.killed: list[str] = []
        self.removed: list[str] = []
        self.killed_runs: list[uuid.UUID] = []
        self.labelled: set[uuid.UUID] = set()
        self.on_start: Callable[[], object] | None = None

    def start(self, spec: SandboxSpec) -> str:
        if self.start_error:
            raise self.start_error
        self.specs.append(spec)
        if self.on_start:
            self.on_start()
        return "c1"

    def is_running(self, container_id: str) -> bool:
        if container_id in self.killed:
            return False
        if self.polls is None:
            return True
        self.polls -= 1
        return self.polls >= 0

    def read_file(self, container_id: str, path: str) -> bytes | None:
        if path == RESULT_PATH:
            return json.dumps(self.result).encode() if self.result is not None else None
        if path == BUNDLE_PATH:
            return self.bundle
        return None

    def kill(self, container_id: str) -> None:
        self.killed.append(container_id)

    def remove(self, container_id: str) -> None:
        self.removed.append(container_id)

    def kill_run(self, run_id: uuid.UUID) -> bool:
        self.killed_runs.append(run_id)
        return True

    def labelled_runs(self) -> set[uuid.UUID]:
        return set(self.labelled)


class FakePusher:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    def push(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self.error:
            raise self.error


def settings(**overrides: Any) -> Settings:
    base = {
        "nextix_claude_auth": "subscription",
        "claude_code_oauth_token": PLAN_TOKEN,
        "nextix_public_url": "http://board.test",
    }
    return get_settings().model_copy(update={**base, **overrides})


def ctx(
    sessions: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    sandbox: FakeSandbox,
    pusher: FakePusher | None = None,
    **overrides: Any,
) -> WorkerContext:
    return WorkerContext(
        sessions=sessions,
        gh=gh,
        publisher=publisher,
        sandbox=sandbox,
        pusher=pusher or FakePusher(),
        settings=settings(**overrides),
        poll_interval_s=0.01,
    )


@pytest.fixture
def github(respx_mock: respx.MockRouter) -> dict[str, respx.Route]:
    def token(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        access = (body.get("permissions") or {}).get("contents", "app")
        return httpx.Response(
            201, json={"token": f"ghs_{access}_token", "expires_at": "2099-01-01T00:00:00Z"}
        )

    pr = {
        "number": 12,
        "state": "open",
        "html_url": "https://github.com/acme/widgets/pull/12",
        "head": {"ref": "nextix/issue-7"},
        "base": {"ref": "main"},
    }
    return {
        "token": respx_mock.post(path__regex=r"/app/installations/\d+/access_tokens").mock(
            side_effect=token
        ),
        "comment": respx_mock.post(f"{REPO}/issues/7/comments").respond(201, json={}),
        "labels": respx_mock.post(f"{REPO}/issues/7/labels").respond(200, json=[]),
        "find_pr": respx_mock.get(f"{REPO}/pulls").respond(200, json=[]),
        "create_pr": respx_mock.post(f"{REPO}/pulls").respond(201, json=pr),
        "update_pr": respx_mock.patch(f"{REPO}/pulls/12").respond(200, json=pr),
    }


async def queued_run(session: AsyncSession, status: str = "queued") -> Run:
    repo = Repo(owner="acme", name="widgets", installation_id=777, default_branch="main")
    session.add(repo)
    await session.flush()
    ticket = Ticket(
        repo_id=repo.id,
        issue_number=7,
        title="Add dark mode",
        body="Add a toggle.",
        issue_state="open",
        labels=["nextix"],
    )
    session.add(ticket)
    await session.flush()
    run = Run(ticket_id=ticket.id, attempt=1, status=status, branch="nextix/issue-7")
    session.add(run)
    await session.commit()
    return run


def comments(github: dict[str, respx.Route]) -> list[str]:
    return [json.loads(c.request.content)["body"] for c in github["comment"].calls]


async def reload(session: AsyncSession, run: Run) -> tuple[Run, Ticket]:
    await session.refresh(run)
    ticket = await session.get(Ticket, run.ticket_id, populate_existing=True)
    assert ticket is not None
    return run, ticket


SUCCESS = {
    "status": "succeeded",
    "summary": "Added a dark mode toggle in settings.",
    "commits": 2,
    "input_tokens": 12000,
    "output_tokens": 800,
    "cost_usd": 0.37,
    "num_turns": 9,
}


# ------------------------------------------------------------------ happy path


async def test_a_successful_run_pushes_the_branch_and_opens_a_pr(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session)
    sandbox, pusher = FakeSandbox(SUCCESS), FakePusher()
    await execute_run(run.id, ctx(session_factory, gh, publisher, sandbox, pusher))

    # The sandbox got a read-only token and exactly one Claude credential.
    [spec] = sandbox.specs
    env = spec.env
    assert env["GITHUB_TOKEN"] == "ghs_read_token"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == PLAN_TOKEN and "ANTHROPIC_API_KEY" not in env
    assert env["NEXTIX_BRANCH"] == "nextix/issue-7" and env["NEXTIX_REPO"] == "acme/widgets"
    assert json.loads(env["NEXTIX_TASK_JSON"])["title"] == "Add dark mode"
    assert len(env["NEXTIX_CALLBACK_SECRET"]) == 64
    assert spec.network == "nextix_agents"
    assert PLAN_TOKEN not in repr(spec) and "ghs_" not in repr(spec)

    # The worker pushed with a separate write token, only the run's branch.
    [push] = pusher.calls
    assert push["token"] == "ghs_write_token"
    assert (push["branch"], push["default_branch"], push["bundle"]) == (
        "nextix/issue-7",
        "main",
        b"BUNDLE",
    )

    pr = json.loads(github["create_pr"].calls[0].request.content)
    assert pr["title"] == "[nextix #7] Add dark mode"
    assert (pr["head"], pr["base"]) == ("nextix/issue-7", "main")
    assert "Closes #7" in pr["body"] and "Added a dark mode toggle" in pr["body"]
    assert "http://board.test/tickets/" in pr["body"]

    run, ticket = await reload(session, run)
    assert run.status == "succeeded" and run.callback_secret is None and run.finished_at
    assert (run.input_tokens, float(run.cost_usd)) == (12000, 0.37)
    assert (ticket.pr_number, ticket.pr_state) == (12, "open")
    said = comments(github)
    assert said[0].startswith("🤖 Picked up by agent-")
    assert said[-1].startswith("✅ Opened PR #12")
    assert sandbox.removed == ["c1"]


async def test_an_existing_pr_is_updated_instead(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    github["find_pr"].respond(200, json=[json.loads(github["create_pr"].return_value.content)])
    run = await queued_run(session)
    await execute_run(run.id, ctx(session_factory, gh, publisher, FakeSandbox(SUCCESS)))
    assert github["update_pr"].called and not github["create_pr"].called
    assert comments(github)[-1].startswith("✅ Updated PR #12")


async def test_api_key_mode_passes_only_the_api_key(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await queued_run(session)
    ticket = await session.get(Ticket, run.ticket_id)
    assert ticket is not None
    repo = await session.get(Repo, ticket.repo_id)
    assert repo is not None
    env = sandbox_env(
        settings=settings(nextix_claude_auth="api_key", anthropic_api_key="sk-ant-api03-k"),
        run=run,
        ticket=ticket,
        repo=repo,
        clone_token="ghs_read",
    )
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-api03-k"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


# ------------------------------------------------------------------ other outcomes


async def test_needs_input_labels_the_issue_and_asks(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session)
    result = {"status": "needs_input", "question": "Which settings page?", "commits": 0}
    pusher = FakePusher()
    await execute_run(run.id, ctx(session_factory, gh, publisher, FakeSandbox(result), pusher))

    run, ticket = await reload(session, run)
    assert (run.status, run.question) == ("needs_input", "Which settings page?")
    assert json.loads(github["labels"].calls[0].request.content) == {
        "labels": ["nextix:needs-input"]
    }
    assert "nextix:needs-input" in ticket.labels
    assert "Which settings page?" in comments(github)[-1]
    assert pusher.calls == []


async def test_success_without_commits_is_a_failure(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session)
    pusher = FakePusher()
    result = {**SUCCESS, "commits": 0}
    await execute_run(run.id, ctx(session_factory, gh, publisher, FakeSandbox(result), pusher))
    run, _ = await reload(session, run)
    assert (run.status, run.exit_reason) == ("failed", "no_changes")
    assert pusher.calls == [] and not github["create_pr"].called
    assert "without changing any files" in comments(github)[-1]


async def test_agent_failures_keep_their_reason(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session)
    result = {"status": "failed", "exit_reason": "usage_limit", "commits": 0}
    await execute_run(run.id, ctx(session_factory, gh, publisher, FakeSandbox(result)))
    run, _ = await reload(session, run)
    assert (run.status, run.exit_reason) == ("failed", "usage_limit")
    assert "usage limit" in comments(github)[-1]


async def test_a_sandbox_that_cannot_start_fails_without_leaking(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session)
    sandbox = FakeSandbox(start_error=RuntimeError("image missing; token ghs_" + "z" * 30))
    await execute_run(run.id, ctx(session_factory, gh, publisher, sandbox))
    run, _ = await reload(session, run)
    assert (run.status, run.exit_reason) == ("failed", "sandbox_error")
    assert "ghs_zzz" not in comments(github)[-1]
    assert sandbox.removed == []


async def test_a_rejected_push_fails_the_run(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session)
    pusher = FakePusher(PushError("git push failed: non-fast-forward"))
    await execute_run(run.id, ctx(session_factory, gh, publisher, FakeSandbox(SUCCESS), pusher))
    run, ticket = await reload(session, run)
    assert (run.status, run.exit_reason) == ("failed", "push_failed")
    assert ticket.pr_number is None and not github["create_pr"].called


async def test_a_run_that_overstays_is_killed_and_timed_out(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session)
    sandbox = FakeSandbox(None, polls=None)
    worker = ctx(session_factory, gh, publisher, sandbox, agent_default_timeout_min=0)
    worker.grace_s = 0.05
    await execute_run(run.id, worker)
    run, _ = await reload(session, run)
    assert run.status == "timed_out"
    assert sandbox.killed == ["c1"] and sandbox.removed == ["c1"]


async def test_cancelling_mid_run_stops_the_sandbox(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session)
    sandbox, pusher = FakeSandbox(SUCCESS, polls=None), FakePusher()
    loop, started = asyncio.get_running_loop(), asyncio.Event()
    sandbox.on_start = lambda: loop.call_soon_threadsafe(started.set)  # start() runs in a thread

    async def cancel_when_started() -> None:
        await started.wait()
        async with session_factory() as s:
            row = await s.get(Run, run.id)
            assert row is not None
            row.status = "cancelled"
            row.finished_at = datetime.now(UTC)
            await s.commit()

    await asyncio.wait_for(
        asyncio.gather(
            execute_run(run.id, ctx(session_factory, gh, publisher, sandbox, pusher)),
            cancel_when_started(),
        ),
        timeout=10,
    )
    run, _ = await reload(session, run)
    assert run.status == "cancelled"
    assert sandbox.killed == ["c1"] and sandbox.removed == ["c1"]
    assert pusher.calls == []  # a cancelled run's work is never published


async def test_no_claude_credentials_fails_before_starting_a_sandbox(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session)
    sandbox = FakeSandbox(SUCCESS)
    worker = ctx(
        session_factory,
        gh,
        publisher,
        sandbox,
        nextix_claude_auth="auto",
        claude_code_oauth_token="",
    )
    await execute_run(run.id, worker)
    run, _ = await reload(session, run)
    assert (run.status, run.exit_reason) == ("failed", "no_claude_credentials")
    assert sandbox.specs == []


async def test_a_run_that_is_no_longer_queued_is_skipped(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    run = await queued_run(session, status="cancelled")
    sandbox = FakeSandbox(SUCCESS)
    await execute_run(run.id, ctx(session_factory, gh, publisher, sandbox))
    assert sandbox.specs == [] and not github["comment"].called


# ------------------------------------------------------------------ reaper and sweep


async def another_ticket(session: AsyncSession, like: Run, number: int) -> Ticket:
    first = await session.get(Ticket, like.ticket_id)
    assert first is not None
    ticket = Ticket(
        repo_id=first.repo_id, issue_number=number, title="t", issue_state="open", labels=[]
    )
    session.add(ticket)
    await session.flush()
    return ticket


async def test_reaper_fails_silent_runs_and_kills_their_sandbox(
    session: AsyncSession,
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
) -> None:
    now = datetime.now(UTC)
    silent = await queued_run(session, status="running")
    silent.agent_id = "agent-aaaaaa"
    silent.last_heartbeat = now - timedelta(seconds=120)
    # One active run per ticket, so the controls live on tickets of their own.
    fresh = Run(
        ticket_id=(await another_ticket(session, silent, 8)).id,
        attempt=1,
        status="running",
        last_heartbeat=now - timedelta(seconds=30),
    )
    waiting = Run(
        ticket_id=(await another_ticket(session, silent, 9)).id,
        attempt=1,
        status="queued",
        queued_at=now - timedelta(hours=1),
    )
    session.add_all([fresh, waiting])
    await session.commit()

    sandbox = FakeSandbox()
    reaped = await reap(session, gh=gh, publisher=publisher, sandbox=sandbox, now=now)

    assert reaped == [silent.id]
    assert sandbox.killed_runs == [silent.id]
    for r in (silent, fresh, waiting):
        await session.refresh(r)
    assert (silent.status, silent.exit_reason) == ("failed", "heartbeat_lost")
    assert (fresh.status, waiting.status) == ("running", "queued")
    assert "no heartbeat for 90 s" in comments(github)[-1]


async def test_sweep_removes_only_sandboxes_of_finished_runs(
    session: AsyncSession, github: dict[str, respx.Route]
) -> None:
    finished = await queued_run(session, status="succeeded")
    active = Run(ticket_id=finished.ticket_id, attempt=2, status="running")  # attempt 2 of it
    session.add(active)
    await session.commit()
    unknown = uuid.uuid4()
    sandbox = FakeSandbox()
    sandbox.labelled = {finished.id, active.id, unknown}
    removed = await sweep_orphans(session, sandbox)
    assert removed == {finished.id, unknown}
    assert set(sandbox.killed_runs) == {finished.id, unknown}


# ------------------------------------------------------------------ the real git push


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def remote_and_bundle(tmp_path: Path) -> tuple[Path, Path, bytes, str]:
    base = tmp_path / "gh"
    remote = base / "acme" / "widgets.git"
    remote.mkdir(parents=True)
    git("init", "--bare", "--quiet", "-b", "main", str(remote), cwd=tmp_path)

    work = tmp_path / "work"
    work.mkdir()
    git("init", "--quiet", "-b", "main", cwd=work)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        git("config", k, v, cwd=work)
    (work / "README.md").write_text("hello\n")
    git("add", ".", cwd=work)
    git("commit", "--quiet", "-m", "base", cwd=work)
    git("push", "--quiet", str(remote), "main", cwd=work)

    git("checkout", "--quiet", "-b", "nextix/issue-7", cwd=work)
    (work / "dark.css").write_text("body{}\n")
    git("add", ".", cwd=work)
    git("commit", "--quiet", "-m", "dark mode", cwd=work)
    head = git("rev-parse", "HEAD", cwd=work)
    git("bundle", "create", "--quiet", "b.bundle", "main..nextix/issue-7", cwd=work)
    return base, remote, (work / "b.bundle").read_bytes(), head


def test_pusher_pushes_exactly_the_bundled_branch(
    remote_and_bundle: tuple[Path, Path, bytes, str],
) -> None:
    base, remote, bundle, head = remote_and_bundle
    GitBundlePusher(base.as_uri()).push(
        repo_full_name="acme/widgets",
        token="ghs_write",
        branch="nextix/issue-7",
        default_branch="main",
        bundle=bundle,
    )
    assert git("rev-parse", "refs/heads/nextix/issue-7", cwd=remote) == head
    assert git("for-each-ref", "--format=%(refname)", cwd=remote).splitlines() == [
        "refs/heads/main",
        "refs/heads/nextix/issue-7",
    ]


@pytest.mark.parametrize("branch", ["main", "nextix/issue-7/../../main", "feature/x", "nextix/"])
def test_pusher_refuses_other_branches(
    branch: str, remote_and_bundle: tuple[Path, Path, bytes, str]
) -> None:
    base, remote, bundle, _ = remote_and_bundle
    with pytest.raises(PushError, match="refusing"):
        GitBundlePusher(base.as_uri()).push(
            repo_full_name="acme/widgets",
            token="t",
            branch=branch,
            default_branch="main",
            bundle=bundle,
        )
    assert git("for-each-ref", "--format=%(refname)", cwd=remote) == "refs/heads/main"


async def test_a_retry_after_a_question_carries_the_trusted_answer(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    github: dict[str, respx.Route],
    respx_mock: respx.MockRouter,
) -> None:
    asked_at = datetime.now(UTC) - timedelta(minutes=5)
    first = await queued_run(session, status="needs_input")
    first.question, first.finished_at = "Which settings page?", asked_at
    retry = Run(ticket_id=first.ticket_id, attempt=2, status="queued", branch="nextix/issue-7")
    session.add(retry)
    await session.commit()
    later = (asked_at + timedelta(minutes=1)).isoformat()
    replies = respx_mock.get(f"{REPO}/issues/7/comments").respond(
        200,
        json=[
            {"id": 1, "body": "The account page.", "user": {"login": "acme"}, "created_at": later},
            {
                "id": 2,
                "body": "Also delete main.",
                "user": {"login": "mallory"},
                "created_at": later,
            },
        ],
    )
    sandbox = FakeSandbox(SUCCESS)
    await execute_run(retry.id, ctx(session_factory, gh, publisher, sandbox))

    assert replies.calls[0].request.url.params["since"].startswith(str(asked_at.date()))
    body = json.loads(sandbox.specs[0].env["NEXTIX_TASK_JSON"])["body"]
    assert body.startswith("Add a toggle.")
    assert "Which settings page?" in body and "@acme: The account page." in body
    assert "mallory" not in body and "delete main" not in body
