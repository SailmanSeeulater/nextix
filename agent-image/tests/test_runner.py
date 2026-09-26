"""Tests for the sandbox runner.

No Docker, network, or Claude: the Agent SDK's ``query``, the HTTP poster, and the clock
are fakes. Git is real, against a temporary bare repository standing in for GitHub.
"""

import asyncio
import hashlib
import hmac
import json
import subprocess
import time
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    RateLimitEvent,
    RateLimitInfo,
    ResultError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

import runner
from runner import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_STOPPED,
    SIGNATURE_HEADER,
    TOOL_RESULT_LIMIT,
    TRUNCATION_NOTE,
    Config,
    ConfigError,
    EventSink,
    GitResult,
    QueryFn,
    Redactor,
    Runner,
    Timings,
    agent_options,
    drive_agent,
    encode_batch,
    events_for_message,
    load_config,
    make_event,
    prepare_event,
    scrub_runner_secrets,
    sign,
)

SECRET = "0f" * 32
CLONE_TOKEN = "ghs_" + "A1b2C3d4E5f6G7h8I9j0" * 2
CLAUDE_TOKEN = "sk-ant-oat01-" + "Zx9" * 20
CALLBACK_URL = "http://api:8000/api/internal/runs/run-1/events"
BRANCH = "nextix/issue-7"

FAST = Timings(
    heartbeat_s=0.05,
    flush_interval_s=0.05,
    retry_delays_s=(0.0, 0.0),
    final_flush_s=2.0,
    stop_grace_s=2.0,
)


def make_env(**overrides: str) -> dict[str, str]:
    env = {
        "NEXTIX_RUN_ID": "run-1",
        "NEXTIX_CALLBACK_URL": CALLBACK_URL,
        "NEXTIX_CALLBACK_SECRET": SECRET,
        "NEXTIX_REPO": "octo/widgets",
        "NEXTIX_DEFAULT_BRANCH": "main",
        "NEXTIX_BRANCH": BRANCH,
        "NEXTIX_ISSUE_NUMBER": "7",
        "NEXTIX_TASK_JSON": json.dumps(
            {
                "title": "Add greeting",
                "body": "Create hello.txt saying hello.",
                "extra_instructions": "Keep it short.",
                "review_comments": [],
            }
        ),
        "NEXTIX_MODEL": "claude-opus-5",
        "NEXTIX_MAX_TURNS": "60",
        "NEXTIX_TIMEOUT_MIN": "30",
        "NEXTIX_MAX_COST_USD": "3",
        "NEXTIX_ALLOWED_TOOLS": "Read,Edit,Write,Bash,Glob,Grep",
        "GITHUB_TOKEN": CLONE_TOKEN,
        "CLAUDE_CODE_OAUTH_TOKEN": CLAUDE_TOKEN,
    }
    env.update(overrides)
    return env


def make_config(**overrides: str) -> Config:
    return load_config(make_env(**overrides))


# --------------------------------------------------------------------------- SDK fakes


def assistant(*blocks: Any, error: Any = None) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model="claude-opus-5", error=error)


def result_message(
    subtype: str = "success",
    *,
    is_error: bool = False,
    text: str | None = "Added hello.txt with a greeting.",
    api_error_status: int | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1200,
        duration_api_ms=1100,
        is_error=is_error,
        num_turns=4,
        session_id="session-1",
        total_cost_usd=0.42,
        usage={"input_tokens": 1200, "output_tokens": 340, "cache_read_input_tokens": 9000},
        result=text,
        api_error_status=api_error_status,
    )


class ScriptedQuery:
    """A stand-in for ``claude_agent_sdk.query`` that plays back a script."""

    def __init__(
        self,
        *messages: Any,
        edit: Callable[[Path], None] | None = None,
        delay: float = 0.0,
        hang: bool = False,
        raise_after: BaseException | None = None,
    ) -> None:
        self.messages = messages
        self.edit = edit
        self.delay = delay
        self.hang = hang
        self.raise_after = raise_after
        self.prompt: str | None = None
        self.options: ClaudeAgentOptions | None = None
        self.closed = False

    async def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        self.prompt = prompt
        self.options = options
        try:
            if self.edit is not None:
                self.edit(Path(str(options.cwd)))
            if self.delay:
                await asyncio.sleep(self.delay)
            for message in self.messages:
                yield message
            if self.hang:
                await asyncio.sleep(3600)
            if self.raise_after is not None:
                raise self.raise_after
        finally:
            self.closed = True


class FakePoster:
    def __init__(self, respond: Callable[[list[dict[str, Any]]], int] | None = None) -> None:
        self.requests: list[tuple[str, bytes, dict[str, str]]] = []
        self.respond = respond

    async def __call__(self, url: str, body: bytes, headers: Mapping[str, str]) -> int:
        self.requests.append((url, body, dict(headers)))
        if self.respond is None:
            return 200
        return self.respond(json.loads(body)["events"])

    @property
    def events(self) -> list[dict[str, Any]]:
        return [e for _, body, _ in self.requests for e in json.loads(body)["events"]]

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.events]


class StepClock:
    """The first reading is the run's start; every later one is ``elapsed`` seconds on."""

    def __init__(self, elapsed: float) -> None:
        self.elapsed = elapsed
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return 0.0 if self.calls == 1 else self.elapsed


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


# --------------------------------------------------------------------------- git fixtures


def git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout


def seed_commit(repo: Path, message: str) -> None:
    git("add", "-A", cwd=repo)
    git(
        "-c",
        "user.name=Seed",
        "-c",
        "user.email=seed@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "-m",
        message,
        cwd=repo,
    )


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A bare 'GitHub' repo with one commit on main."""
    bare = tmp_path / "origin.git"
    git("init", "--quiet", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    seed = tmp_path / "seed"
    git("init", "--quiet", "-b", "main", str(seed), cwd=tmp_path)
    (seed / "README.md").write_text("# widgets\n", encoding="utf-8")
    seed_commit(seed, "init")
    git("remote", "add", "origin", str(bare), cwd=seed)
    git("push", "--quiet", "origin", "main", cwd=seed)
    return bare


def make_runner(
    tmp_path: Path,
    origin: Path,
    query: QueryFn,
    poster: FakePoster | None = None,
    *,
    cfg: Config | None = None,
    clock: Callable[[], float] = time.monotonic,
    git_runner: Any = runner.run_git,
) -> tuple[Runner, FakePoster]:
    poster = poster or FakePoster()
    instance = Runner(
        cfg or make_config(),
        query_fn=query,
        poster=poster,
        git=git_runner,
        clock=clock,
        sleep=no_sleep,
        work_dir=tmp_path / "work",
        remote_url=str(origin),
        timings=FAST,
        warn=lambda _text: None,
    )
    return instance, poster


def read_result(tmp_path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (tmp_path / "work" / ".nextix-out" / "result.json").read_text(encoding="utf-8")
    )
    return data


def bundle_path(tmp_path: Path) -> Path:
    return tmp_path / "work" / ".nextix-out" / "branch.bundle"


RESULT_FIELDS = {
    "status",
    "summary",
    "question",
    "commits",
    "exit_reason",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "num_turns",
}


# --------------------------------------------------------------------------- signing


def test_signature_is_hmac_sha256_of_the_raw_body() -> None:
    body = encode_batch([make_event("heartbeat"), make_event("log", text="Cloning...")])
    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert sign(SECRET, body) == f"sha256={expected}"
    assert json.loads(body) == {
        "events": [
            {"kind": "heartbeat", "payload": {}},
            {"kind": "log", "payload": {"text": "Cloning..."}},
        ]
    }


async def test_every_post_is_signed_over_the_exact_body() -> None:
    poster = FakePoster()
    sink = EventSink(
        url=CALLBACK_URL,
        secret=SECRET,
        poster=poster,
        redactor=Redactor(),
        sleep=no_sleep,
        timings=FAST,
        on_gone=lambda: None,
        warn=lambda _text: None,
    )
    for n in range(45):
        sink.add(make_event("log", text=f"line {n}"))
    await sink.flush()

    assert [len(json.loads(body)["events"]) for _, body, _ in poster.requests] == [20, 20, 5]
    for url, body, headers in poster.requests:
        assert url == CALLBACK_URL
        assert headers["Content-Type"] == "application/json"
        digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert headers[SIGNATURE_HEADER] == f"sha256={digest}"


async def test_transient_failures_are_retried_and_client_errors_are_not() -> None:
    answers = iter([503, 200, 400])
    delays: list[float] = []

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    poster = FakePoster(lambda _events: next(answers))
    sink = EventSink(
        url=CALLBACK_URL,
        secret=SECRET,
        poster=poster,
        redactor=Redactor(),
        sleep=record_sleep,
        timings=Timings(retry_delays_s=(0.5, 1.0)),
        on_gone=lambda: None,
        warn=lambda _text: None,
    )
    sink.add(make_event("log", text="first"))
    await sink.flush()
    assert len(poster.requests) == 2  # 503, then delivered
    assert delays == [0.5]

    sink.add(make_event("log", text="second"))
    await sink.flush()
    assert len(poster.requests) == 3  # 400 is not retried
    assert delays == [0.5]


async def test_batches_are_numbered_and_a_retry_resends_the_same_number() -> None:
    answers = iter([503, 200, 200])
    poster = FakePoster(lambda _events: next(answers))
    sink = EventSink(
        url=CALLBACK_URL,
        secret=SECRET,
        poster=poster,
        redactor=Redactor(),
        sleep=no_sleep,
        timings=Timings(retry_delays_s=(0.5,)),
        on_gone=lambda: None,
        warn=lambda _text: None,
    )
    sink.add(make_event("log", text="first"))
    await sink.flush()
    sink.add(make_event("log", text="second"))
    await sink.flush()
    numbers = [json.loads(body)["batch"] for _, body, _ in poster.requests]
    assert numbers == [1, 1, 2]  # the API stores a resent batch only once


def test_nul_characters_are_dropped() -> None:
    assert Redactor().text("a\x00b") == "ab"


# --------------------------------------------------------------------------- event mapping


def test_assistant_text_and_tool_use_map_to_message_and_tool_use() -> None:
    message = assistant(
        TextBlock(text="Looking at the README."),
        ToolUseBlock(id="toolu_1", name="Read", input={"file_path": "README.md"}),
        TextBlock(text="   "),
    )
    assert events_for_message(message) == [
        {"kind": "message", "payload": {"text": "Looking at the README."}},
        {
            "kind": "tool_use",
            "payload": {"id": "toolu_1", "name": "Read", "input": {"file_path": "README.md"}},
        },
    ]


def test_assistant_error_also_becomes_an_error_event() -> None:
    events = events_for_message(assistant(TextBlock(text="API Error"), error="rate_limit"))
    assert events[-1] == {"kind": "error", "payload": {"text": "Claude API error: rate_limit"}}


def test_tool_results_inside_user_messages_map_to_tool_result() -> None:
    message = UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id="toolu_1",
                content=[{"type": "text", "text": "line one"}, {"type": "text", "text": "two"}],
                is_error=False,
            ),
            ToolResultBlock(tool_use_id="toolu_2", content="exit 1", is_error=True),
            ToolResultBlock(tool_use_id="toolu_3", content=None),
        ]
    )
    assert events_for_message(message) == [
        {
            "kind": "tool_result",
            "payload": {"tool_use_id": "toolu_1", "content": "line one\ntwo", "is_error": False},
        },
        {
            "kind": "tool_result",
            "payload": {"tool_use_id": "toolu_2", "content": "exit 1", "is_error": True},
        },
        {
            "kind": "tool_result",
            "payload": {"tool_use_id": "toolu_3", "content": "", "is_error": False},
        },
    ]
    assert events_for_message(UserMessage(content="a plain prompt")) == []


def test_result_message_maps_to_usage() -> None:
    assert events_for_message(result_message()) == [
        {
            "kind": "usage",
            "payload": {"input_tokens": 10200, "output_tokens": 340, "cost_usd": 0.42},
        }
    ]


def test_other_messages_are_not_sent() -> None:
    assert events_for_message(SystemMessage(subtype="init", data={"tools": ["Read"]})) == []


# --------------------------------------------------------------------------- truncation, redaction


def test_tool_result_content_is_truncated_to_20k() -> None:
    event = make_event("tool_result", tool_use_id="t", content="x" * 50_000, is_error=False)
    content = prepare_event(event, Redactor())["payload"]["content"]
    assert len(content) <= TOOL_RESULT_LIMIT
    assert content.endswith(TRUNCATION_NOTE)


def test_tokens_are_redacted_before_truncation() -> None:
    # A token straddling the cut must not leave a usable prefix behind.
    content = "x" * (TOOL_RESULT_LIMIT - 30) + " " + CLONE_TOKEN
    event = make_event("tool_result", tool_use_id="t", content=content, is_error=False)
    out = prepare_event(event, Redactor())["payload"]["content"]
    assert "ghs_" not in out
    assert len(out) <= TOOL_RESULT_LIMIT


def test_redaction_covers_token_shapes_and_run_secrets() -> None:
    redactor = Redactor([SECRET])
    text = (
        f"key sk-ant-api03-abcDEF_123 pat ghp_{'a' * 36} app {CLONE_TOKEN} "
        f"fine github_pat_{'B' * 40} url https://x-access-token:hunter22@github.com/o/r "
        f"secret {SECRET}"
    )
    out = redactor.text(text)
    for leaked in ("sk-ant-api03", "ghp_", "ghs_", "github_pat_", "hunter22", SECRET):
        assert leaked not in out
    assert "[REDACTED]" in out


def test_runner_redacts_the_clone_token_in_its_header_form(tmp_path: Path) -> None:
    instance, _ = make_runner(tmp_path, tmp_path / "origin.git", ScriptedQuery())
    encoded = runner.github_auth_env(CLONE_TOKEN)["GIT_CONFIG_VALUE_0"].rsplit(" ", 1)[-1]
    assert encoded not in instance.redactor.text(f"header was {encoded}")


def test_nested_tool_input_is_redacted() -> None:
    event = make_event(
        "tool_use",
        id="t",
        name="Bash",
        input={"command": f"echo {CLAUDE_TOKEN}", "env": [{"v": CLONE_TOKEN}]},
    )
    payload = prepare_event(event, Redactor())["payload"]
    assert CLAUDE_TOKEN not in json.dumps(payload)
    assert CLONE_TOKEN not in json.dumps(payload)


# --------------------------------------------------------------------------- config, prompt


def test_config_reads_the_contract() -> None:
    cfg = make_config(NEXTIX_ALLOWED_TOOLS="Read, Edit,Bash(npm test:*),Bash(git diff:*)")
    assert cfg.repo == "octo/widgets"
    assert cfg.issue_number == 7
    assert cfg.max_cost_usd == 3.0
    assert cfg.allowed_tools == ("Read", "Edit", "Bash(npm test:*)", "Bash(git diff:*)")
    assert cfg.tool_names == ["Read", "Edit", "Bash"]
    assert cfg.task.title == "Add greeting"
    assert CLONE_TOKEN not in repr(cfg)
    assert SECRET not in repr(cfg)


def test_config_errors_name_variables_not_values() -> None:
    env = make_env()
    del env["NEXTIX_MODEL"]
    env["CLAUDE_CODE_OAUTH_TOKEN"] = ""
    with pytest.raises(ConfigError, match="NEXTIX_MODEL") as info:
        load_config(env)
    assert CLONE_TOKEN not in str(info.value)
    with pytest.raises(ConfigError, match="no Claude credential"):
        load_config(make_env(CLAUDE_CODE_OAUTH_TOKEN=""))
    with pytest.raises(ConfigError, match="NEXTIX_BRANCH"):
        load_config(make_env(NEXTIX_BRANCH="--upload-pack=evil"))
    with pytest.raises(ConfigError, match="NEXTIX_REPO"):
        load_config(make_env(NEXTIX_REPO="not a repo"))


def test_agent_options_follow_the_contract(tmp_path: Path) -> None:
    task = {
        "title": "Add greeting",
        "body": "Create hello.txt.",
        "extra_instructions": "Use British spelling.",
        "review_comments": ["Rename the file", {"path": "hello.txt", "line": 1, "body": "Wave"}],
    }
    cfg = make_config(NEXTIX_TASK_JSON=json.dumps(task), NEXTIX_MAX_TURNS="12")
    options = agent_options(cfg, tmp_path / "repo")
    assert options.permission_mode == "bypassPermissions"
    assert options.setting_sources == []
    assert options.tools == ["Read", "Edit", "Write", "Bash", "Glob", "Grep"]
    assert options.allowed_tools == ["Read", "Edit", "Write", "Bash", "Glob", "Grep"]
    assert options.max_turns == 12
    assert options.max_budget_usd == 3.0
    assert options.model == "claude-opus-5"
    assert options.cwd == str(tmp_path / "repo")
    assert options.env == {}  # the Claude credential is inherited unchanged
    prompt = options.system_prompt
    assert isinstance(prompt, dict)
    assert prompt["type"] == "preset"
    append = prompt["append"]
    for expected in (
        "Issue #7: Add greeting",
        "Create hello.txt.",
        "Use British spelling.",
        "- Rename the file",
        "hello.txt:1: Wave",
        ".nextix/needs-input.md",
        ".github/workflows",
        "Never push",
    ):
        assert expected in append


def test_a_huge_issue_cannot_overflow_the_command_line(tmp_path: Path) -> None:
    # The SDK passes the system prompt as one argv entry; Linux rejects one over 128 KiB.
    task = {"title": "Big", "body": "界" * 200_000, "review_comments": ["x" * 50_000]}
    cfg = make_config(NEXTIX_TASK_JSON=json.dumps(task))
    prompt = agent_options(cfg, tmp_path / "repo").system_prompt
    assert isinstance(prompt, dict)
    assert prompt["type"] == "preset"
    append = prompt["append"]
    assert len(append.encode("utf-8")) <= runner.SYSTEM_PROMPT_MAX_BYTES
    assert append.endswith(TRUNCATION_NOTE)
    assert ".nextix/needs-input.md" in append  # the rules survive the cut
    assert "Issue #7: Big" in append


def test_runner_secrets_are_scrubbed_but_the_claude_credential_stays() -> None:
    env = make_env()
    assert sorted(scrub_runner_secrets(env)) == ["GITHUB_TOKEN", "NEXTIX_CALLBACK_SECRET"]
    assert "GITHUB_TOKEN" not in env
    assert "NEXTIX_CALLBACK_SECRET" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == CLAUDE_TOKEN


# --------------------------------------------------------------------------- SDK outcomes


async def run_drive(query: ScriptedQuery) -> tuple[runner.AgentOutcome, list[runner.Event]]:
    emitted: list[runner.Event] = []
    outcome = await drive_agent(
        query,
        prompt="p",
        options=ClaudeAgentOptions(),
        emit=emitted.append,
        max_turns=60,
        max_cost_usd=3.0,
    )
    return outcome, emitted


async def test_success_result_is_the_summary() -> None:
    outcome, emitted = await run_drive(ScriptedQuery(result_message()))
    assert outcome.status == "succeeded"
    assert outcome.summary == "Added hello.txt with a greeting."
    assert outcome.usage == runner.Usage(10200, 340, 0.42, 4)  # cache reads count as input
    assert [e["kind"] for e in emitted] == ["usage"]


@pytest.mark.parametrize(
    ("subtype", "reason"),
    [("error_max_turns", "max_turns"), ("error_max_budget_usd", "max_cost")],
)
async def test_limits_map_to_failed_with_exit_reason(subtype: str, reason: str) -> None:
    # The SDK yields the error result, then raises ResultError as the CLI exits non-zero.
    query = ScriptedQuery(
        result_message(subtype, is_error=True, text=None),
        raise_after=ResultError("failed", {"subtype": subtype}),
    )
    outcome, _ = await run_drive(query)
    assert (outcome.status, outcome.exit_reason) == ("failed", reason)
    assert outcome.usage.cost_usd == 0.42


async def test_rejected_rate_limit_event_stops_with_usage_limit() -> None:
    info = RateLimitInfo(status="rejected", resets_at=1_790_000_000, rate_limit_type="five_hour")
    query = ScriptedQuery(
        RateLimitEvent(rate_limit_info=info, uuid="u", session_id="s"),
        hang=True,
    )
    outcome, emitted = await asyncio.wait_for(run_drive(query), 5)
    assert (outcome.status, outcome.exit_reason) == ("failed", "usage_limit")
    assert "5-hour usage limit" in outcome.summary
    assert "resets at" in outcome.summary
    assert emitted == []  # the runner reports the failure, once
    assert query.closed


async def test_usage_limit_error_result_maps_to_usage_limit() -> None:
    query = ScriptedQuery(
        result_message(
            is_error=True, text="You've hit your limit · resets 5pm", api_error_status=429
        ),
        raise_after=ResultError("failed", {"subtype": "success", "api_error_status": 429}),
    )
    outcome, _ = await run_drive(query)
    assert (outcome.status, outcome.exit_reason) == ("failed", "usage_limit")


class FakeClient:
    """Stands in for ClaudeSDKClient to check that sdk_query always disconnects."""

    instances: list["FakeClient"] = []

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options = options
        self.prompt: str | None = None
        self.disconnected = False
        FakeClient.instances.append(self)

    async def connect(self) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self.prompt = prompt

    async def receive_response(self) -> AsyncIterator[Any]:
        info = RateLimitInfo(status="rejected", rate_limit_type="seven_day")
        yield RateLimitEvent(rate_limit_info=info, uuid="u", session_id="s")
        await asyncio.sleep(3600)

    async def disconnect(self) -> None:
        self.disconnected = True


async def test_sdk_query_disconnects_when_the_run_stops_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "ClaudeSDKClient", FakeClient)
    FakeClient.instances.clear()
    outcome = await asyncio.wait_for(
        drive_agent(
            runner.sdk_query,
            prompt="Resolve issue #7",
            options=ClaudeAgentOptions(),
            emit=lambda _event: None,
            max_turns=60,
            max_cost_usd=3.0,
        ),
        5,
    )
    assert outcome.exit_reason == "usage_limit"
    (client,) = FakeClient.instances
    assert client.prompt == "Resolve issue #7"
    assert client.disconnected


async def test_sdk_crash_without_a_result_is_an_agent_error() -> None:
    query = ScriptedQuery(raise_after=ClaudeSDKError("CLI exited"))
    outcome, _ = await run_drive(query)
    assert (outcome.status, outcome.exit_reason) == ("failed", "agent_error")


def api_retry(error: str, status: int, attempt: int) -> SystemMessage:
    data = {
        "type": "system",
        "subtype": "api_retry",
        "attempt": attempt,
        "max_retries": 10,
        "retry_delay_ms": 500,
        "error_status": status,
        "error": error,
    }
    return SystemMessage(subtype="api_retry", data=data)


def test_api_retries_are_logged() -> None:
    assert events_for_message(api_retry("server_error", 529, 1)) == [
        {
            "kind": "log",
            "payload": {
                "text": "Claude API error (529 server_error); Claude Code is retrying "
                "(attempt 1 of 10)."
            },
        }
    ]


async def test_a_rejected_credential_stops_without_waiting_out_the_retries() -> None:
    # Claude Code retries a 401 ten times over several minutes; the runner stops early.
    query = ScriptedQuery(
        api_retry("authentication_failed", 401, 1),
        api_retry("authentication_failed", 401, 2),
        hang=True,
    )
    outcome, emitted = await asyncio.wait_for(run_drive(query), 5)
    assert (outcome.status, outcome.exit_reason) == ("failed", "auth_failed")
    assert "claude setup-token" in outcome.summary
    assert [e["kind"] for e in emitted] == ["log", "log"]  # the two retries, no error yet
    assert query.closed


async def test_transient_api_errors_keep_going() -> None:
    query = ScriptedQuery(api_retry("server_error", 529, 1), result_message())
    outcome, emitted = await run_drive(query)
    assert outcome.status == "succeeded"
    assert [e["kind"] for e in emitted] == ["log", "usage"]


# --------------------------------------------------------------------------- full runs


def write_greeting(repo: Path) -> None:
    (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
    (repo / ".nextix").mkdir(exist_ok=True)
    (repo / ".nextix" / "notes.md").write_text("scratch\n", encoding="utf-8")
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / "ci.yml").write_text("on: push\n", encoding="utf-8")


async def test_changes_are_committed_and_bundled(tmp_path: Path, origin: Path) -> None:
    query = ScriptedQuery(
        assistant(
            TextBlock(text="Adding the file."),
            ToolUseBlock(id="t1", name="Write", input={"file_path": "hello.txt"}),
        ),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="ok")]),
        result_message(),
        edit=write_greeting,
    )
    instance, poster = make_runner(tmp_path, origin, query)
    code = await instance.run()

    assert code == EXIT_OK
    result = read_result(tmp_path)
    assert set(result) == RESULT_FIELDS
    assert result == {
        "status": "succeeded",
        "summary": "Added hello.txt with a greeting.",
        "question": None,
        "commits": 1,
        "exit_reason": None,
        "input_tokens": 10200,  # 1200 fresh + 9000 read from the prompt cache
        "output_tokens": 340,
        "cost_usd": 0.42,
        "num_turns": 4,
    }

    bundle = bundle_path(tmp_path)
    assert f"refs/heads/{BRANCH}" in git("bundle", "list-heads", str(bundle), cwd=tmp_path)
    check = tmp_path / "check"
    git("clone", "--quiet", "--branch", BRANCH, str(bundle), str(check), cwd=tmp_path)
    assert (check / "hello.txt").read_text(encoding="utf-8") == "hello\n"
    assert not (check / ".nextix").exists()
    assert not (check / ".github").exists()  # CI changes are left out
    log = git("log", "-1", "--format=%an <%ae>|%s", cwd=check).strip()
    assert log == "nexTix agent <nextix-agent@users.noreply.github.com>|nextix: Add greeting (#7)"

    repo = tmp_path / "work" / "repo"
    assert CLONE_TOKEN not in (repo / ".git" / "config").read_text(encoding="utf-8")
    assert git("remote", "get-url", "origin", cwd=repo).strip() == str(origin)

    kinds = poster.kinds()
    assert kinds.index("state") < kinds.index("message") < kinds.index("tool_use")
    assert {"heartbeat", "log", "tool_result", "usage"} <= set(kinds)
    assert {"kind": "state", "payload": {"status": "running"}} in poster.events
    assert query.options is not None and query.options.cwd == str(repo)


async def test_a_leaked_credential_is_never_bundled(tmp_path: Path, origin: Path) -> None:
    def leak(repo: Path) -> None:
        # Committed by the agent itself, then deleted: still in the branch's history.
        (repo / "debug.env").write_text(f"TOKEN={CLAUDE_TOKEN}\n", encoding="utf-8")
        git("add", "debug.env", cwd=repo)
        git("commit", "--quiet", "-m", "debug", cwd=repo)
        (repo / "debug.env").unlink()
        (repo / "hello.txt").write_text("hello\n", encoding="utf-8")

    instance, poster = make_runner(tmp_path, origin, ScriptedQuery(result_message(), edit=leak))
    assert await instance.run() == EXIT_FAILED
    result = read_result(tmp_path)
    assert (result["status"], result["exit_reason"], result["commits"]) == (
        "failed",
        "secret_in_changes",
        0,
    )
    assert not bundle_path(tmp_path).exists()
    assert CLAUDE_TOKEN not in json.dumps(poster.events)


async def test_rerun_continues_the_existing_branch(tmp_path: Path, origin: Path) -> None:
    seed = tmp_path / "seed"
    git("checkout", "--quiet", "-b", BRANCH, cwd=seed)
    (seed / "first.txt").write_text("from the first run\n", encoding="utf-8")
    seed_commit(seed, "nextix: first attempt")
    git("push", "--quiet", "origin", BRANCH, cwd=seed)

    def second_edit(repo: Path) -> None:
        assert (repo / "first.txt").exists()  # started from the existing branch
        (repo / "second.txt").write_text("review fix\n", encoding="utf-8")

    query = ScriptedQuery(result_message(), edit=second_edit)
    instance, _ = make_runner(tmp_path, origin, query)
    assert await instance.run() == EXIT_OK

    assert read_result(tmp_path)["commits"] == 1  # ahead of the remote branch
    check = tmp_path / "check"
    git(
        "clone", "--quiet", "--branch", BRANCH, str(bundle_path(tmp_path)), str(check), cwd=tmp_path
    )
    assert git("rev-list", "--count", "HEAD", cwd=check).strip() == "3"


async def test_no_changes_succeeds_with_zero_commits(tmp_path: Path, origin: Path) -> None:
    instance, _ = make_runner(tmp_path, origin, ScriptedQuery(result_message()))
    assert await instance.run() == EXIT_OK
    result = read_result(tmp_path)
    assert (result["status"], result["commits"]) == ("succeeded", 0)
    assert not bundle_path(tmp_path).exists()


async def test_needs_input_reports_the_question_and_commits_nothing(
    tmp_path: Path, origin: Path
) -> None:
    def ask(repo: Path) -> None:
        (repo / "README.md").write_text("half-done edit\n", encoding="utf-8")
        (repo / ".nextix").mkdir()
        (repo / ".nextix" / "needs-input.md").write_text(
            f"Which greeting? (token {CLONE_TOKEN})\n", encoding="utf-8"
        )

    query = ScriptedQuery(result_message(text="I need to know the greeting."), edit=ask)
    instance, _ = make_runner(tmp_path, origin, query)
    assert await instance.run() == EXIT_OK

    result = read_result(tmp_path)
    assert result["status"] == "needs_input"
    assert result["question"] == "Which greeting? (token [REDACTED])"
    assert result["commits"] == 0
    assert result["exit_reason"] is None
    assert not bundle_path(tmp_path).exists()
    repo = tmp_path / "work" / "repo"
    assert git("rev-list", "--count", "refs/remotes/origin/main..HEAD", cwd=repo).strip() == "0"


async def test_hitting_max_turns_commits_nothing(tmp_path: Path, origin: Path) -> None:
    query = ScriptedQuery(
        result_message("error_max_turns", is_error=True, text=None),
        raise_after=ResultError("failed", {"subtype": "error_max_turns"}),
        edit=write_greeting,
    )
    instance, _ = make_runner(tmp_path, origin, query)
    assert await instance.run() == EXIT_FAILED
    result = read_result(tmp_path)
    assert (result["status"], result["exit_reason"], result["commits"]) == (
        "failed",
        "max_turns",
        0,
    )
    assert not bundle_path(tmp_path).exists()


async def test_a_usage_limit_is_reported_once(tmp_path: Path, origin: Path) -> None:
    info = RateLimitInfo(status="rejected", rate_limit_type="seven_day")
    query = ScriptedQuery(RateLimitEvent(rate_limit_info=info, uuid="u", session_id="s"), hang=True)
    instance, poster = make_runner(tmp_path, origin, query)
    assert await asyncio.wait_for(instance.run(), 30) == EXIT_FAILED
    result = read_result(tmp_path)
    assert (result["status"], result["exit_reason"]) == ("failed", "usage_limit")
    errors = [e for e in poster.events if e["kind"] == "error"]
    assert [e["payload"]["text"] for e in errors] == [result["summary"]]


async def test_timeout_stops_the_agent(tmp_path: Path, origin: Path) -> None:
    # One minute allowed; the clock says 59.7 s are already gone after the clone.
    query = ScriptedQuery(assistant(TextBlock(text="thinking")), hang=True)
    cfg = make_config(NEXTIX_TIMEOUT_MIN="1")
    instance, _ = make_runner(tmp_path, origin, query, cfg=cfg, clock=StepClock(59.7))

    started = time.monotonic()
    code = await asyncio.wait_for(instance.run(), 30)
    assert time.monotonic() - started < 20
    assert code == EXIT_FAILED
    result = read_result(tmp_path)
    assert (result["status"], result["exit_reason"]) == ("timed_out", "timeout")
    assert query.closed


async def test_410_stops_the_run_quickly(tmp_path: Path, origin: Path) -> None:
    def respond(events: list[dict[str, Any]]) -> int:
        return 410 if any(e["kind"] == "tool_use" for e in events) else 200

    query = ScriptedQuery(
        assistant(ToolUseBlock(id="t1", name="Bash", input={"command": "sleep 999"})),
        hang=True,
    )
    instance, poster = make_runner(tmp_path, origin, query, FakePoster(respond))

    started = time.monotonic()
    code = await asyncio.wait_for(instance.run(), 30)
    assert time.monotonic() - started < 20
    assert code == EXIT_STOPPED
    assert instance.stop_reason == "gone"
    assert query.closed
    result = read_result(tmp_path)
    assert (result["status"], result["exit_reason"]) == ("failed", "cancelled")
    # Nothing is posted after the 410.
    last = json.loads(poster.requests[-1][1])["events"]
    assert any(e["kind"] == "tool_use" for e in last)


async def test_result_json_is_written_when_the_runner_crashes(tmp_path: Path, origin: Path) -> None:
    async def exploding_git(
        args: Any, *, cwd: Path | None = None, env: Mapping[str, str] | None = None
    ) -> GitResult:
        raise RuntimeError(f"boom {CLONE_TOKEN}")

    instance, poster = make_runner(
        tmp_path, origin, ScriptedQuery(result_message()), git_runner=exploding_git
    )
    assert await instance.run() == EXIT_FAILED
    result = read_result(tmp_path)
    assert set(result) == RESULT_FIELDS
    assert (result["status"], result["exit_reason"]) == ("failed", "runner_error")
    assert CLONE_TOKEN not in json.dumps(result)
    assert "error" in poster.kinds()
    assert CLONE_TOKEN not in json.dumps(poster.events)


async def test_clone_failure_is_reported(tmp_path: Path) -> None:
    missing = tmp_path / "nope.git"
    instance, _ = make_runner(tmp_path, missing, ScriptedQuery(result_message()))
    assert await instance.run() == EXIT_FAILED
    result = read_result(tmp_path)
    assert (result["status"], result["exit_reason"]) == ("failed", "clone_failed")


async def test_bad_environment_still_writes_result_json(tmp_path: Path) -> None:
    env = make_env()
    del env["NEXTIX_TASK_JSON"]
    code = await runner.amain(env, work_dir=tmp_path / "work")
    assert code == EXIT_FAILED
    result = read_result(tmp_path)
    assert (result["status"], result["exit_reason"]) == ("failed", "runner_error")
    assert "NEXTIX_TASK_JSON" in result["summary"]


async def test_heartbeats_flow_for_the_whole_run(tmp_path: Path, origin: Path) -> None:
    query = ScriptedQuery(result_message(), delay=0.5)
    instance, poster = make_runner(tmp_path, origin, query)
    assert await instance.run() == EXIT_OK
    heartbeats = [e for e in poster.events if e["kind"] == "heartbeat"]
    assert len(heartbeats) >= 4
    assert all(e["payload"] == {} for e in heartbeats)
