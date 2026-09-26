"""Tests for the Phase 4 runner steps: setup, tests, screenshots, diffs, and the manifest.

No Docker, network, Claude, or browser: the shell, the HTTP probe and the browser are
fakes, and git is real (a temporary bare repository stands in for GitHub). The tests of
the real process-group handling need a POSIX system and are skipped elsewhere; the
Chromium test runs only where Playwright's browsers are installed (the sandbox image).
"""

import asyncio
import contextlib
import io
import json
import os
import signal
import sys
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from PIL import Image, ImageDraw
from pixelmatch.contrib.PIL import pixelmatch

import runner
from runner import (
    EXIT_FAILED,
    EXIT_OK,
    TEST_REPORT_BYTES,
    Artifact,
    Artifacts,
    CaptureError,
    CommandResult,
    Config,
    ConfigError,
    DiffTooSlow,
    ProcessShell,
    ProjectConfig,
    Redactor,
    Route,
    Runner,
    SetupReport,
    TailBuffer,
    Timings,
    Viewport,
    agent_options,
    changed_dependency_files,
    clean_output,
    command_env,
    parse_project_config,
    pixel_diff,
    plan_budget,
    png_size,
    render_test_report,
    route_stems,
    sweep_processes,
)
from test_runner import (
    CLAUDE_TOKEN,
    CLONE_TOKEN,
    SECRET,
    FakePoster,
    ScriptedQuery,
    StepClock,
    git,
    make_config,
    make_env,
    no_sleep,
    read_result,
    result_message,
    seed_commit,
)

posix_only = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")

FAST = Timings(
    heartbeat_s=0.05,
    flush_interval_s=0.05,
    retry_delays_s=(0.0, 0.0),
    final_flush_s=2.0,
    stop_grace_s=2.0,
    app_poll_s=0.01,
)
SMALL = {"width": 320, "height": 240}
APP: dict[str, Any] = {
    "start": "node server.js",
    "port": 3000,
    "ready_path": "/health",
    "ready_timeout_s": 30,
    "screenshots": [
        {"path": "/", "viewport": SMALL},
        {"path": "/settings", "viewport": SMALL},
        {"path": "/missing", "viewport": SMALL},
    ],
}


def project_env(**project: Any) -> str:
    return json.dumps({"setup": [], "test": None, "app": None, **project})


def png(size: tuple[int, int] = (320, 240), *, square: bool = False) -> bytes:
    image = Image.new("RGB", size, (255, 255, 255))
    if square:
        ImageDraw.Draw(image).rectangle((50, 50, 59, 59), fill=(220, 20, 20))  # 10x10
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- fakes


class FakeService:
    def __init__(self, cwd: Path, env: Mapping[str, str], exit_code: int | None) -> None:
        self.cwd = cwd
        self.env = dict(env)
        self.exit_code = exit_code
        self.stopped = False

    @property
    def returncode(self) -> int | None:
        return -15 if self.stopped else self.exit_code

    def output(self) -> bytes:
        return f"Error: cannot find module (token {CLONE_TOKEN})\n".encode()

    async def stop(self) -> None:
        self.stopped = True


class FakeShell:
    """Answers commands from a table (default: success) and records what ran where."""

    def __init__(
        self,
        results: Mapping[str, CommandResult] | None = None,
        *,
        app_exit_code: int | None = None,
        effects: Mapping[str, Callable[[Path], None]] | None = None,
    ) -> None:
        self.results = dict(results or {})
        self.app_exit_code = app_exit_code
        self.effects = dict(effects or {})
        self.runs: list[tuple[str, Path, dict[str, str], float]] = []
        self.services: list[FakeService] = []

    @property
    def running(self) -> FakeService | None:
        live = [s for s in self.services if not s.stopped and s.exit_code is None]
        return live[-1] if live else None

    async def run(
        self,
        command: str,
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_s: float,
        keep_bytes: int,
    ) -> CommandResult:
        self.runs.append((command, Path(cwd), dict(env), timeout_s))
        effect = self.effects.get(command)
        if effect is not None:
            effect(Path(cwd))
        return self.results.get(command, CommandResult(0, b"ok\n", duration_s=1.25))

    async def start(
        self, command: str, *, cwd: Path, env: Mapping[str, str], keep_bytes: int
    ) -> FakeService:
        service = FakeService(Path(cwd), env, self.app_exit_code)
        self.services.append(service)
        return service

    def ran(self, where: str) -> list[str]:
        return [command for command, cwd, _, _ in self.runs if cwd.name == where]


class FakeProbe:
    def __init__(self, shell: FakeShell, *, answers: bool = True, busy: bool = False) -> None:
        self.shell = shell
        self.answers = answers
        self.busy = busy
        self.urls: list[str] = []

    async def status(self, url: str) -> int | None:
        self.urls.append(url)
        return 404 if self.answers and self.shell.running is not None else None

    async def listening(self, port: int) -> bool:
        return self.busy


Picture = Callable[[str, str], bytes]  # (checkout directory name, route path) -> PNG


def before_and_after(where: str, path: str) -> bytes:
    if path == "/missing":
        raise CaptureError("the page answered HTTP 404")
    return png(square=(where == "repo" and path == "/settings"))


class FakeBrowser:
    def __init__(self, shell: FakeShell, picture: Picture = before_and_after) -> None:
        self.shell = shell
        self.picture = picture
        self.envs: list[dict[str, str]] = []
        self.urls: list[str] = []

    def __call__(self, env: Mapping[str, str]) -> Any:
        return self._session(env)

    @asynccontextmanager
    async def _session(self, env: Mapping[str, str]) -> AsyncIterator["FakeBrowser"]:
        self.envs.append(dict(env))
        yield self

    async def screenshot(self, url: str, viewport: Viewport) -> bytes:
        self.urls.append(url)
        service = self.shell.running
        assert service is not None, "screenshot taken while the app was not running"
        path = url.split(":3000", 1)[1]
        return self.picture(service.cwd.name, path)


class TickClock:
    """Every reading is ``step`` seconds after the one before."""

    def __init__(self, step: float) -> None:
        self.step = step
        self.now = 0.0

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A bare 'GitHub' repo whose main branch holds a tiny app."""
    bare = tmp_path / "origin.git"
    git("init", "--quiet", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    seed = tmp_path / "seed"
    git("init", "--quiet", "-b", "main", str(seed), cwd=tmp_path)
    (seed / "package.json").write_text('{"name": "widgets"}\n', encoding="utf-8")
    (seed / "server.js").write_text("// serves the pages\n", encoding="utf-8")
    seed_commit(seed, "init")
    git("remote", "add", "origin", str(bare), cwd=seed)
    git("push", "--quiet", "origin", "main", cwd=seed)
    return bare


def environ_with_secrets() -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/home/agent",
        "LANG": "C.UTF-8",
        "GITHUB_TOKEN": CLONE_TOKEN,
        "NEXTIX_CALLBACK_SECRET": SECRET,
        "CLAUDE_CODE_OAUTH_TOKEN": CLAUDE_TOKEN,
        "ANTHROPIC_API_KEY": "sk-ant-api03-" + "k" * 30,
        "NEXTIX_TASK_JSON": '{"title": "x"}',
        "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright",
        "NPM_TOKEN": "npm_abcdefghijklmnop",
        "SOME_URL": f"https://user:{CLONE_TOKEN}@example.com",
    }


def build(
    tmp_path: Path,
    origin: Path,
    query: ScriptedQuery,
    *,
    project: str,
    shell: FakeShell | None = None,
    probe: FakeProbe | None = None,
    browser: FakeBrowser | None = None,
    clock: Callable[[], float] = time.monotonic,
    sweep: Callable[[], list[int]] | None = None,
    **env: str,
) -> tuple[Runner, FakePoster, FakeShell, FakeProbe, FakeBrowser]:
    shell = shell or FakeShell()
    probe = probe or FakeProbe(shell)
    browser = browser or FakeBrowser(shell)
    poster = FakePoster()
    cfg: Config = make_config(NEXTIX_CONFIG_JSON=project, **env)
    instance = Runner(
        cfg,
        query_fn=query,
        poster=poster,
        clock=clock,
        sleep=no_sleep,
        work_dir=tmp_path / "work",
        remote_url=str(origin),
        timings=FAST,
        warn=lambda _text: None,
        shell=shell,
        probe=probe,
        browser=browser,
        sweep=sweep,
        environ=environ_with_secrets(),
    )
    return instance, poster, shell, probe, browser


def artifacts_dir(tmp_path: Path) -> Path:
    return tmp_path / "work" / ".nextix-out" / "artifacts"


def read_manifest(tmp_path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (artifacts_dir(tmp_path) / "manifest.json").read_text(encoding="utf-8")
    )
    return data


def logs(poster: FakePoster) -> list[str]:
    return [e["payload"]["text"] for e in poster.events if e["kind"] == "log"]


def edit_settings(repo: Path) -> None:
    (repo / "settings.css").write_text("h1 { color: red; }\n", encoding="utf-8")


def no_secrets_in(env: Mapping[str, str]) -> None:
    blob = json.dumps(env)
    for secret in (CLONE_TOKEN, SECRET, CLAUDE_TOKEN, "sk-ant-api03", "npm_abcdef"):
        assert secret not in blob
    for name in ("GITHUB_TOKEN", "NEXTIX_CALLBACK_SECRET", "CLAUDE_CODE_OAUTH_TOKEN"):
        assert name not in env
    assert "PLAYWRIGHT_BROWSERS_PATH" not in env
    assert not any(name.startswith("NEXTIX_") for name in env)


# --------------------------------------------------------------------------- config


def test_missing_config_means_no_setup_test_or_app() -> None:
    assert make_config().project == ProjectConfig()
    assert parse_project_config("") == ProjectConfig()
    assert parse_project_config('{"setup": [], "test": null, "app": null}') == ProjectConfig()


def test_config_follows_the_contract() -> None:
    raw = json.dumps(
        {
            "setup": ["npm ci", "npm run build"],
            "test": "npm test",
            "app": {
                "start": "npm run dev",
                "port": 3000,
                "ready_path": "/",
                "ready_timeout_s": 90,
                "screenshots": [
                    {"path": "/"},
                    {"path": "/settings", "viewport": {"width": 1280, "height": 800}},
                    {"path": "/search?q=blue widgets"},  # the worker allows spaces
                ],
            },
            "future_key": {"ignored": True},
        }
    )
    project = parse_project_config(raw)
    assert project.setup == ("npm ci", "npm run build")
    assert project.test == "npm test"
    assert project.app is not None
    assert project.app.start == "npm run dev"
    assert project.app.port == 3000
    assert project.app.screenshots == (
        Route("/", Viewport(1280, 800)),
        Route("/settings", Viewport(1280, 800)),
        Route("/search?q=blue widgets", Viewport(1280, 800)),
    )
    assert project.has_post_agent_steps


@pytest.mark.parametrize(
    ("raw", "problem"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"setup": "npm ci"}', "setup"),
        (json.dumps({"setup": ["x"] * 21}), "setup"),
        ('{"test": ""}', "test"),
        (json.dumps({"test": "x" * 1001}), "test"),
        (json.dumps({"app": {**APP, "port": 0}}), "app.port"),
        (json.dumps({"app": {**APP, "port": "3000"}}), "app.port"),
        (json.dumps({"app": {**APP, "ready_timeout_s": 1}}), "app.ready_timeout_s"),
        (json.dumps({"app": {**APP, "screenshots": []}}), "app.screenshots"),
        (json.dumps({"app": {**APP, "screenshots": [{"path": "settings"}]}}), "path"),
        (json.dumps({"app": {**APP, "screenshots": [{"path": "/a\nb"}]}}), "path"),
        (json.dumps({"app": {**APP, "ready_path": "health"}}), "app.ready_path"),
        (
            json.dumps({"app": {**APP, "screenshots": [{"path": "/", "viewport": {"width": 9}}]}}),
            "viewport.width",
        ),
    ],
)
def test_malformed_config_is_rejected_clearly(raw: str, problem: str) -> None:
    with pytest.raises(ConfigError, match="NEXTIX_CONFIG_JSON") as info:
        parse_project_config(raw)
    assert problem in str(info.value)


async def test_malformed_config_is_a_runner_error_not_a_crash(tmp_path: Path) -> None:
    env = make_env(NEXTIX_CONFIG_JSON='{"app": {"port": 3000')
    code = await runner.amain(env, work_dir=tmp_path / "work")
    assert code == EXIT_FAILED
    result = read_result(tmp_path)
    assert (result["status"], result["exit_reason"]) == ("failed", "runner_error")
    assert "NEXTIX_CONFIG_JSON is not valid JSON" in result["summary"]
    assert result["tests"] is None


# --------------------------------------------------------------------------- time budget


def test_the_agent_leaves_time_for_tests_and_screenshots() -> None:
    with_tests = ProjectConfig(test="npm test")
    budget = plan_budget(100.0, 30, with_tests)
    assert budget.reserve_s == 540  # 30% of 30 min
    assert budget.agent == 100 + 1800 - 540
    assert budget.run == 100 + 1800
    assert budget.setup == 100 + 1260 * 0.5  # before the agent: at most half its window
    assert budget.before == 100 + 1260 * 0.5 * 0.6
    assert plan_budget(0.0, 120, with_tests).reserve_s == 600  # capped at 10 minutes
    assert plan_budget(0.0, 30, ProjectConfig(setup=("npm ci",))).agent == 1800


async def test_the_agent_is_stopped_early_to_leave_room_for_the_tests(
    tmp_path: Path, origin: Path
) -> None:
    # One minute in all; 18 s kept for the tests, so the agent's deadline is at 42 s.
    query = ScriptedQuery(hang=True)
    instance, _, shell, _, _ = build(
        tmp_path,
        origin,
        query,
        project=project_env(test="npm test"),
        clock=StepClock(41.8),
        NEXTIX_TIMEOUT_MIN="1",
    )
    assert await asyncio.wait_for(instance.run(), 30) == EXIT_FAILED
    result = read_result(tmp_path)
    assert (result["status"], result["exit_reason"]) == ("timed_out", "timeout")
    assert "kept for tests and screenshots" in result["summary"]
    assert result["tests"] is None  # the agent did not succeed
    assert shell.runs == []


# --------------------------------------------------------------------------- environment


def test_command_environment_has_no_secrets() -> None:
    base = environ_with_secrets()
    redactor = Redactor([CLONE_TOKEN, SECRET, CLAUDE_TOKEN])
    env = command_env(base, redactor, {"PORT": "3000"})
    no_secrets_in(env)
    assert "SOME_URL" not in env  # its value holds the clone token
    assert "NPM_TOKEN" not in env  # named like a secret
    assert env["PATH"] == base["PATH"]
    assert env["HOME"] == "/home/agent"
    assert env["CI"] == "true"
    assert env["PORT"] == "3000"


# --------------------------------------------------------------------------- output


def test_tail_buffer_keeps_the_end() -> None:
    tail = TailBuffer(10)
    for chunk in (b"0123456", b"789abc", b"def"):
        tail.feed(chunk)
    assert tail.data() == b"6789abcdef"
    assert tail.total == 16
    assert tail.truncated


def test_output_is_cleaned_and_redacted() -> None:
    raw = (
        f"half a tok{CLONE_TOKEN[10:]}\n"  # a token cut by truncation: the line goes
        "\x1b[31mFAIL\x1b[0m src/app.test.ts\n"
        "progress 10%\rprogress 100%\n"
        f"using {CLAUDE_TOKEN}\x00\n"
    ).encode()
    text = clean_output(raw, truncated=True, redactor=Redactor([CLAUDE_TOKEN]))
    assert text == "FAIL src/app.test.ts\nprogress 100%\nusing [REDACTED]\n"


def test_test_report_notes_truncation_and_timeouts() -> None:
    long = CommandResult(1, b"cut line\nlast line\n", truncated=True, duration_s=3.0)
    report = render_test_report(long, Redactor())
    assert report.startswith("[nexTix: the output was longer than 200 KB; only the end is kept]")
    assert "cut line" not in report
    assert report.endswith("last line\n")
    slow = CommandResult(124, b"", duration_s=600.0, timed_out=True)
    report = render_test_report(slow, Redactor())
    assert "(the test command printed nothing)" in report
    assert "[nexTix: the tests were stopped after 600 s]" in report


# --------------------------------------------------------------------------- manifest


def artifact(kind: Any, label: str, file: str, size: int, rank: tuple[int, int, int]) -> Artifact:
    return Artifact(kind, label, file, {"width": 1, "height": 1}, b"x" * size, rank)


def test_manifest_shape_and_stale_files(tmp_path: Path) -> None:
    directory = tmp_path / "artifacts"
    (directory / "shots").mkdir(parents=True)
    (directory / "shots" / "planted.png").write_bytes(b"not ours")
    store = Artifacts()
    store.add(artifact("screenshot_after", "/", "shots/index-after.png", 5, (1, 0, 1)))
    store.add(artifact("screenshot_before", "/", "shots/index-before.png", 5, (1, 0, 0)))
    report_meta = {"exit_code": 1, "passed": False, "duration_s": 12.3, "truncated": False}
    store.add(Artifact("test_report", "npm test", "test-report.txt", report_meta, b"FAIL\n"))
    store.error("app_after", None, f"app did not answer on :3000 within 90 s {CLONE_TOKEN}")
    manifest = store.write(directory, Redactor())

    assert manifest == json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == {
        "artifacts": [
            {"kind": "test_report", "label": "npm test", "file": "test-report.txt",
             "meta": report_meta},
            {"kind": "screenshot_before", "label": "/", "file": "shots/index-before.png",
             "meta": {"width": 1, "height": 1}},
            {"kind": "screenshot_after", "label": "/", "file": "shots/index-after.png",
             "meta": {"width": 1, "height": 1}},
        ],
        "errors": [
            {"step": "app_after", "label": None,
             "message": "app did not answer on :3000 within 90 s [REDACTED]"},
        ],
    }  # fmt: skip
    files = sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*"))
    assert files == [
        "manifest.json",
        "shots",
        "shots/index-after.png",
        "shots/index-before.png",
        "test-report.txt",
    ]


def test_manifest_caps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "ARTIFACT_FILE_MAX_BYTES", 100)
    monkeypatch.setattr(runner, "ARTIFACTS_MAX_BYTES", 250)
    monkeypatch.setattr(runner, "ARTIFACTS_MAX_FILES", 4)  # 3 artifacts + the manifest
    store = Artifacts()
    store.add(artifact("screenshot_before", "/huge", "shots/huge-before.png", 101, (1, 0, 0)))
    for n in range(1, 5):
        store.add(artifact("screenshot_before", f"/r{n}", f"shots/r{n}-before.png", 90, (1, n, 0)))
    manifest = store.write(tmp_path / "artifacts", Redactor())

    kept = [a["file"] for a in manifest["artifacts"]]
    assert kept == ["shots/r1-before.png", "shots/r2-before.png"]  # 180 bytes; 270 > 250
    problems = {e["label"]: e["message"] for e in manifest["errors"]}
    assert "over the 10 MB limit" in problems["/huge"]
    assert "60 MB" in problems["/r3"]
    assert all(e["step"] == "artifacts" for e in manifest["errors"])
    assert not (tmp_path / "artifacts" / "shots" / "huge-before.png").exists()


@posix_only
def test_an_unwritable_planted_directory_does_not_block_the_artifacts(tmp_path: Path) -> None:
    directory = tmp_path / "artifacts"
    locked = directory / "shots" / "locked"
    locked.mkdir(parents=True)
    (locked / "x.png").write_bytes(b"planted")
    locked.chmod(0)
    (directory / "shots").chmod(0o500)
    try:
        store = Artifacts()
        store.add(artifact("screenshot_before", "/", "shots/index-before.png", 5, (1, 0, 0)))
        manifest = store.write(directory, Redactor())
    finally:
        for path in (directory / "shots", locked):
            with contextlib.suppress(OSError):
                path.chmod(0o700)
    assert [a["file"] for a in manifest["artifacts"]] == ["shots/index-before.png"]
    assert not locked.exists()


def test_the_file_cap_counts_the_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "ARTIFACTS_MAX_FILES", 3)
    store = Artifacts()
    for n in range(4):
        store.add(artifact("screenshot_before", f"/r{n}", f"shots/r{n}-before.png", 1, (1, n, 0)))
    manifest = store.write(tmp_path / "artifacts", Redactor())
    assert len(manifest["artifacts"]) == 2
    assert len(list((tmp_path / "artifacts").rglob("*.*"))) == 3


def test_a_real_10_mb_screenshot_is_left_out(tmp_path: Path) -> None:
    store = Artifacts()
    size = 10 * 1024 * 1024 + 1
    store.add(artifact("screenshot_diff", "/", "shots/index-diff.png", size, (1, 0, 2)))
    manifest = store.write(tmp_path / "artifacts", Redactor())
    assert manifest["artifacts"] == []
    assert "over the 10 MB limit" in manifest["errors"][0]["message"]


def test_route_file_names_are_safe_and_distinct() -> None:
    routes = [Route(p) for p in ("/", "/settings", "/a-b", "/a/b", "/Settings?tab=1", "/../x")]
    assert route_stems(routes) == ["index", "settings", "a-b", "a-b-2", "settings-tab-1", "x"]


def test_dependency_files_are_recognised() -> None:
    paths = [
        "src/app.ts",
        "package.json",
        "web/package-lock.json",
        "requirements-dev.txt",
        "backend/requirements.txt",
        "Pipfile.lock",
        "pyproject.toml",
        "docs/pyproject.toml.md",
        "uv.lock",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
    ]
    assert changed_dependency_files(paths) == [
        "package.json",
        "web/package-lock.json",
        "requirements-dev.txt",
        "backend/requirements.txt",
        "Pipfile.lock",
        "pyproject.toml",
        "uv.lock",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
    ]


# --------------------------------------------------------------------------- pixel diffs


def test_identical_screenshots_have_no_diff() -> None:
    diff = pixel_diff(png(), png())
    assert (diff.width, diff.height, diff.diff_pixels, diff.diff_pct) == (320, 240, 0, 0.0)
    assert png_size(diff.png) == (320, 240)


def test_completely_different_screenshots() -> None:
    white = io.BytesIO()
    Image.new("RGB", (20, 10), (255, 255, 255)).save(white, format="PNG")
    black = io.BytesIO()
    Image.new("RGB", (20, 10), (0, 0, 0)).save(black, format="PNG")
    diff = pixel_diff(white.getvalue(), black.getvalue())
    assert (diff.diff_pixels, diff.diff_pct) == (200, 100.0)
    with Image.open(io.BytesIO(diff.png)) as image:
        assert image.convert("RGBA").getpixel((5, 5)) == (255, 0, 0, 255)  # red: changed


def test_a_small_change_is_counted_exactly() -> None:
    diff = pixel_diff(png(), png(square=True))
    assert diff.diff_pixels == 100  # the 10x10 square
    assert diff.diff_pct == 0.13  # 100 / 76800, as a percentage, 2 decimals
    with Image.open(io.BytesIO(diff.png)) as image:
        rgba = image.convert("RGBA")
        assert rgba.getpixel((55, 55)) == (255, 0, 0, 255)
        assert rgba.getpixel((200, 200)) == (255, 255, 255, 255)  # unchanged white, faded


def test_screenshots_of_different_sizes_are_padded() -> None:
    diff = pixel_diff(png((20, 10)), png((20, 20)))
    assert (diff.width, diff.height) == (20, 20)
    # The before image's missing bottom half is neutral grey; the after image is white.
    assert (diff.diff_pixels, diff.diff_pct) == (200, 50.0)


def test_diffing_only_the_changed_box_matches_pixelmatch_on_the_whole_image() -> None:
    # Anti-aliased text, shifted and recoloured, so the anti-aliasing check matters.
    first = Image.new("RGBA", (200, 120), (250, 250, 250, 255))
    second = first.copy()
    ImageDraw.Draw(first).text((20, 30), "Settings saved", fill=(20, 20, 20, 255))
    ImageDraw.Draw(second).text((21, 30), "Settings saved", fill=(20, 20, 20, 255))
    ImageDraw.Draw(second).text((20, 80), "New line", fill=(200, 30, 30, 255))
    whole = pixelmatch(first, second, None, threshold=0.1, includeAA=False)
    with_aa = pixelmatch(first, second, None, threshold=0.1, includeAA=True)
    encoded = []
    for image in (first, second):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded.append(buffer.getvalue())
    diff = pixel_diff(encoded[0], encoded[1])
    assert diff.diff_pixels == whole
    assert whole <= with_aa  # anti-aliased pixels are detected and not counted


def test_a_diff_that_would_take_too_long_is_refused() -> None:
    with pytest.raises(DiffTooSlow):
        pixel_diff(png(), png(square=True), max_pixels=50)


def test_png_size_rejects_other_files() -> None:
    with pytest.raises(ValueError, match="not a PNG"):
        png_size(b"GIF89a....")


# --------------------------------------------------------------------------- the prompt


def appended_prompt(options: ClaudeAgentOptions | None) -> str:
    """The text nexTix appends to Claude Code's system prompt."""
    assert options is not None
    prompt = options.system_prompt
    assert isinstance(prompt, dict)
    assert prompt["type"] == "preset"
    return prompt["append"]


def test_the_prompt_tells_the_agent_about_setup_tests_and_the_app(tmp_path: Path) -> None:
    cfg = make_config(NEXTIX_CONFIG_JSON=project_env(setup=["npm ci"], test="npm test", app=APP))
    text = appended_prompt(agent_options(cfg, tmp_path, setup=SetupReport(("npm ci",))))
    assert "already ran the project's setup in the repository (`npm ci`)" in text
    assert "The project's test command is `npm test`" in text
    assert "/, /settings, /missing before and after your change" in text
    assert "Keep it short." in text  # extra_instructions still arrive
    assert text.index("## Project setup and tests") < text.index("## Issue #7")


def test_the_prompt_reports_a_failed_setup(tmp_path: Path) -> None:
    cfg = make_config(NEXTIX_CONFIG_JSON=project_env(setup=["npm ci", "npm run build"]))
    report = SetupReport(
        ("npm ci", "npm run build"),
        failed="npm ci",
        detail="exited with 1 after 12 s",
        tail="npm ERR! missing lockfile",
        skipped=1,
    )
    text = appended_prompt(agent_options(cfg, tmp_path, setup=report))
    assert "Setup failed: `npm ci` exited with 1 after 12 s." in text
    assert "The 1 setup command(s) after it did not run." in text
    assert "    npm ERR! missing lockfile" in text


# --------------------------------------------------------------------------- full runs


async def test_before_and_after_screenshots_diffs_and_tests(tmp_path: Path, origin: Path) -> None:
    seen: dict[str, Any] = {}

    def agent_edit(repo: Path) -> None:
        # The before worktree is gone before the agent starts, record and files.
        seen["base_exists"] = (repo.parent / "base").exists()
        seen["worktrees"] = git("worktree", "list", "--porcelain", cwd=repo)
        edit_settings(repo)

    project = project_env(setup=["npm ci"], test="npm test", app=APP)
    query = ScriptedQuery(result_message(), edit=agent_edit)
    sweeps: list[str] = []

    def sweep() -> list[int]:
        sweeps.append("swept")
        return [4242]

    instance, poster, shell, probe, browser = build(
        tmp_path, origin, query, project=project, sweep=sweep
    )
    assert await instance.run() == EXIT_OK

    # Order of work: setup in the base checkout, the before app, setup in the repo, the
    # agent, the tests, the after app.
    assert shell.ran("base") == ["npm ci"]
    assert [command for command, _, _, _ in shell.runs] == ["npm ci", "npm ci", "npm test"]
    assert [s.cwd.name for s in shell.services] == ["base", "repo"]
    assert all(s.stopped for s in shell.services)  # the app is always stopped
    assert seen["base_exists"] is False
    assert seen["worktrees"].count("worktree ") == 1
    assert not (tmp_path / "work" / "base").exists()
    assert len(sweeps) == 3  # after the before app, after the agent, after the checks

    # Nothing the repo runs gets a secret.
    for _, _, env, timeout_s in shell.runs:
        no_secrets_in(env)
        assert timeout_s <= 600
    for service in shell.services:
        no_secrets_in(service.env)
        assert (service.env["PORT"], service.env["HOST"]) == ("3000", "127.0.0.1")
    for env in browser.envs:
        no_secrets_in(env)
    assert probe.urls[0] == "http://127.0.0.1:3000/health"

    result = read_result(tmp_path)
    assert result["status"] == "succeeded"
    assert result["commits"] == 1
    assert result["tests"] == {
        "command": "npm test",
        "exit_code": 0,
        "passed": True,
        "duration_s": 1.2,
    }

    manifest = read_manifest(tmp_path)
    summary = [(a["kind"], a["label"], a["file"]) for a in manifest["artifacts"]]
    assert summary == [
        ("test_report", "npm test", "test-report.txt"),
        ("screenshot_before", "/", "shots/index-before.png"),
        ("screenshot_after", "/", "shots/index-after.png"),
        ("screenshot_diff", "/", "shots/index-diff.png"),
        ("screenshot_before", "/settings", "shots/settings-before.png"),
        ("screenshot_after", "/settings", "shots/settings-after.png"),
        ("screenshot_diff", "/settings", "shots/settings-diff.png"),
    ]
    metas = {(a["kind"], a["label"]): a["meta"] for a in manifest["artifacts"]}
    assert metas[("screenshot_before", "/settings")] == {"width": 320, "height": 240}
    assert metas[("screenshot_diff", "/")] == {
        "width": 320,
        "height": 240,
        "diff_pixels": 0,
        "diff_pct": 0.0,
    }
    assert metas[("screenshot_diff", "/settings")] == {
        "width": 320,
        "height": 240,
        "diff_pixels": 100,
        "diff_pct": 0.13,
    }
    assert metas[("test_report", "npm test")] == {
        "exit_code": 0,
        "passed": True,
        "duration_s": 1.2,
        "truncated": False,
    }
    assert manifest["errors"] == [
        {"step": "screenshot_before", "label": "/missing", "message": "the page answered HTTP 404"},
        {"step": "screenshot_after", "label": "/missing", "message": "the page answered HTTP 404"},
    ]
    for item in manifest["artifacts"]:
        path = artifacts_dir(tmp_path) / item["file"]
        assert path.is_file()
        if item["file"].endswith(".png"):
            assert png_size(path.read_bytes()) == (320, 240)
    assert (artifacts_dir(tmp_path) / "test-report.txt").read_text(encoding="utf-8") == "ok\n"

    lines = logs(poster)
    for expected in (
        "Running setup on main (1/1): npm ci",
        "Running setup (1/1): npm ci",
        "Captured 2 of 3 before screenshot(s).",
        "Running tests: npm test",
        "Tests passed (exit 0, 1.2 s).",
        "/settings: 0.13% of pixels changed.",
        "/: no visible change.",
        "Stopped 1 process(es) the agent left running.",
        "Stopped 1 process(es) the tests or the app left running.",
    ):
        assert expected in lines, expected
    assert sum(line.startswith("Bundled 1 commit(s)") for line in lines) == 1  # untouched


async def test_a_setup_failure_is_logged_recorded_and_told_to_the_agent(
    tmp_path: Path, origin: Path
) -> None:
    failed = CommandResult(1, f"npm ERR! {CLONE_TOKEN}\nnpm ERR! missing\n".encode(), False, 12.3)
    shell = FakeShell({"npm ci": failed})
    project = project_env(setup=["npm ci", "npm run build"], test="npm test")
    query = ScriptedQuery(result_message(), edit=edit_settings)
    instance, poster, _, _, _ = build(tmp_path, origin, query, project=project, shell=shell)
    assert await instance.run() == EXIT_OK  # a failed setup never fails the run

    prompt = appended_prompt(query.options)
    assert "Setup failed: `npm ci` exited with 1 after 12 s." in prompt
    assert "npm ERR! missing" in prompt
    assert CLONE_TOKEN not in prompt
    # Setup stops at the first failure, and runs again after the agent because it failed.
    assert [command for command, _, _, _ in shell.runs] == ["npm ci", "npm ci", "npm test"]

    manifest = read_manifest(tmp_path)
    assert manifest["errors"][:2] == [
        {"step": "setup", "label": "npm ci", "message": "npm ci exited with 1 after 12 s"},
        {"step": "setup_rerun", "label": "npm ci", "message": "npm ci exited with 1 after 12 s"},
    ]
    lines = logs(poster)
    assert "Running setup (1/2): npm ci" in lines
    assert any(line.startswith("Setup failed: npm ci (exit 1, 12 s).") for line in lines)
    assert "Skipped the remaining 1 setup command(s)." in lines
    assert CLONE_TOKEN not in json.dumps(poster.events)


async def test_failing_tests_keep_the_end_of_the_output_redacted(
    tmp_path: Path, origin: Path
) -> None:
    noise = b"".join(b"line %06d passes\n" % n for n in range(20_000))  # ~ 400 KB
    tail = noise[-(TEST_REPORT_BYTES - 200) :] + f"FAIL: {CLAUDE_TOKEN}\n".encode()
    shell = FakeShell({"npm test": CommandResult(1, tail, truncated=True, duration_s=12.34)})
    query = ScriptedQuery(result_message(), edit=edit_settings)
    instance, poster, _, _, _ = build(
        tmp_path, origin, query, project=project_env(test="npm test"), shell=shell
    )
    assert await instance.run() == EXIT_OK

    result = read_result(tmp_path)
    assert result["tests"] == {
        "command": "npm test",
        "exit_code": 1,
        "passed": False,
        "duration_s": 12.3,
    }
    report = (artifacts_dir(tmp_path) / "test-report.txt").read_text(encoding="utf-8")
    assert report.startswith("[nexTix: the output was longer than 200 KB")
    assert report.endswith("FAIL: [REDACTED]\n")
    assert CLAUDE_TOKEN not in report
    assert len(report.encode()) <= TEST_REPORT_BYTES + 200
    (item,) = read_manifest(tmp_path)["artifacts"]
    assert item["meta"] == {"exit_code": 1, "passed": False, "duration_s": 12.3, "truncated": True}
    assert "Tests failed (exit 1, 12 s)." in logs(poster)


async def test_setup_runs_again_when_the_agent_changes_dependencies(
    tmp_path: Path, origin: Path
) -> None:
    def add_dependency(repo: Path) -> None:
        (repo / "package.json").write_text('{"name": "widgets", "deps": 1}\n', encoding="utf-8")

    query = ScriptedQuery(result_message(), edit=add_dependency)
    project = project_env(setup=["npm ci"], test="npm test")
    instance, poster, shell, _, _ = build(tmp_path, origin, query, project=project)
    assert await instance.run() == EXIT_OK
    assert [command for command, _, _, _ in shell.runs] == ["npm ci", "npm ci", "npm test"]
    assert "The agent changed package.json, so setup runs again." in logs(poster)
    assert "Running setup again (1/1): npm ci" in logs(poster)


async def test_setup_is_not_repeated_for_other_changes(tmp_path: Path, origin: Path) -> None:
    query = ScriptedQuery(result_message(), edit=edit_settings)
    project = project_env(setup=["npm ci"], test="npm test")
    instance, _, shell, _, _ = build(tmp_path, origin, query, project=project)
    assert await instance.run() == EXIT_OK
    assert [command for command, _, _, _ in shell.runs] == ["npm ci", "npm test"]


async def test_an_app_that_never_answers_is_recorded(tmp_path: Path, origin: Path) -> None:
    shell = FakeShell()
    query = ScriptedQuery(result_message(), edit=edit_settings)
    instance, poster, _, _, browser = build(
        tmp_path,
        origin,
        query,
        project=project_env(app={**APP, "ready_timeout_s": 5}),
        shell=shell,
        probe=FakeProbe(shell, answers=False),
        clock=TickClock(0.25),
    )
    assert await asyncio.wait_for(instance.run(), 30) == EXIT_OK
    assert read_result(tmp_path)["status"] == "succeeded"
    manifest = read_manifest(tmp_path)
    assert manifest["artifacts"] == []
    assert manifest["errors"] == [
        {"step": "app_before", "label": None, "message": "app did not answer on :3000 within 5 s"},
        {"step": "app_after", "label": None, "message": "app did not answer on :3000 within 5 s"},
    ]
    assert all(s.stopped for s in shell.services)
    assert browser.urls == []
    failure = next(line for line in logs(poster) if line.startswith("No screenshots"))
    assert "cannot find module" in failure  # the end of the app's output, for diagnosis
    assert CLONE_TOKEN not in json.dumps(poster.events)


async def test_an_app_that_exits_is_recorded(tmp_path: Path, origin: Path) -> None:
    shell = FakeShell(app_exit_code=1)
    query = ScriptedQuery(result_message(), edit=edit_settings)
    instance, _, _, _, _ = build(tmp_path, origin, query, project=project_env(app=APP), shell=shell)
    assert await instance.run() == EXIT_OK
    errors = read_manifest(tmp_path)["errors"]
    assert errors[0] == {
        "step": "app_before",
        "label": None,
        "message": "the app exited with code 1 before answering on :3000",
    }


async def test_a_busy_port_is_not_mistaken_for_the_app(tmp_path: Path, origin: Path) -> None:
    shell = FakeShell()
    query = ScriptedQuery(result_message(), edit=edit_settings)
    instance, _, _, _, _ = build(
        tmp_path,
        origin,
        query,
        project=project_env(app=APP),
        shell=shell,
        probe=FakeProbe(shell, busy=True),
    )
    assert await instance.run() == EXIT_OK
    assert shell.services == []
    assert read_manifest(tmp_path)["errors"][0]["message"] == (
        "port 3000 is already in use, so the app was not started"
    )


async def test_the_browser_failing_to_start_is_recorded(tmp_path: Path, origin: Path) -> None:
    class BrokenBrowser(FakeBrowser):
        def __call__(self, env: Mapping[str, str]) -> Any:
            raise RuntimeError("Executable doesn't exist at /ms-playwright/chrome")

    shell = FakeShell()
    query = ScriptedQuery(result_message(), edit=edit_settings)
    instance, _, _, _, _ = build(
        tmp_path,
        origin,
        query,
        project=project_env(app=APP),
        shell=shell,
        browser=BrokenBrowser(shell),
    )
    assert await instance.run() == EXIT_OK
    errors = read_manifest(tmp_path)["errors"]
    assert errors[0]["step"] == "screenshot_before"
    assert "the browser failed: RuntimeError: Executable doesn't exist" in errors[0]["message"]
    assert all(s.stopped for s in shell.services)


@pytest.mark.parametrize("kind", ["failed", "needs_input"])
async def test_nothing_is_checked_when_the_agent_does_not_succeed(
    tmp_path: Path, origin: Path, kind: str
) -> None:
    def ask(repo: Path) -> None:
        (repo / ".nextix").mkdir()
        (repo / ".nextix" / "needs-input.md").write_text("Which colour?\n", encoding="utf-8")

    if kind == "failed":
        query = ScriptedQuery(
            result_message("error_max_turns", is_error=True, text=None), edit=edit_settings
        )
    else:
        query = ScriptedQuery(result_message(), edit=ask)
    project = project_env(setup=["npm ci"], test="npm test", app=APP)
    instance, _, shell, _, _ = build(tmp_path, origin, query, project=project)
    await instance.run()

    result = read_result(tmp_path)
    assert result["status"] == ("failed" if kind == "failed" else "needs_input")
    assert result["tests"] is None
    assert "npm test" not in [command for command, _, _, _ in shell.runs]
    assert [s.cwd.name for s in shell.services] == ["base"]  # no after screenshots
    kinds = {a["kind"] for a in read_manifest(tmp_path)["artifacts"]}
    assert kinds == {"screenshot_before"}


async def test_no_changes_skip_the_tests(tmp_path: Path, origin: Path) -> None:
    query = ScriptedQuery(result_message())
    project = project_env(test="npm test")
    instance, poster, shell, _, _ = build(tmp_path, origin, query, project=project)
    assert await instance.run() == EXIT_OK
    assert read_result(tmp_path)["tests"] is None
    assert shell.runs == []
    assert "No changes to check, so the tests and screenshots were skipped." in logs(poster)


async def test_files_made_by_the_tests_are_not_committed(tmp_path: Path, origin: Path) -> None:
    class CoverageShell(FakeShell):
        async def run(
            self,
            command: str,
            *,
            cwd: Path,
            env: Mapping[str, str],
            timeout_s: float,
            keep_bytes: int,
        ) -> CommandResult:
            (Path(cwd) / "coverage.json").write_text("{}", encoding="utf-8")
            return await super().run(
                command, cwd=cwd, env=env, timeout_s=timeout_s, keep_bytes=keep_bytes
            )

    query = ScriptedQuery(result_message(), edit=edit_settings)
    instance, _, _, _, _ = build(
        tmp_path, origin, query, project=project_env(test="npm test"), shell=CoverageShell()
    )
    assert await instance.run() == EXIT_OK
    bundle = tmp_path / "work" / ".nextix-out" / "branch.bundle"
    check = tmp_path / "check"
    git("clone", "--quiet", "--branch", "nextix/issue-7", str(bundle), str(check), cwd=tmp_path)
    assert (check / "settings.css").exists()
    assert not (check / "coverage.json").exists()


async def test_what_setup_leaves_in_the_repo_is_not_committed(tmp_path: Path, origin: Path) -> None:
    def install(repo: Path) -> None:
        (repo / "vendor" / "lib").mkdir(parents=True)
        (repo / "vendor" / "lib" / "index.js").write_text("module.exports = 1\n", encoding="utf-8")
        (repo / "stamp.txt").write_text("built\n", encoding="utf-8")
        (repo / "notes.txt").write_text("generated\n", encoding="utf-8")
        (repo / "package.json").write_text('{"name": "widgets", "lock": 2}\n', encoding="utf-8")

    def agent(repo: Path) -> None:
        edit_settings(repo)
        (repo / "stamp.txt").write_text("changed by the agent\n", encoding="utf-8")

    shell = FakeShell(effects={"npm ci": install})
    query = ScriptedQuery(result_message(), edit=agent)
    instance, poster, _, _, _ = build(
        tmp_path, origin, query, project=project_env(setup=["npm ci"]), shell=shell
    )
    assert await instance.run() == EXIT_OK
    assert read_result(tmp_path)["commits"] == 1
    bundle = tmp_path / "work" / ".nextix-out" / "branch.bundle"
    check = tmp_path / "check"
    git("clone", "--quiet", "--branch", "nextix/issue-7", str(bundle), str(check), cwd=tmp_path)
    committed = git("show", "--name-only", "--format=", "HEAD", cwd=check).split()
    assert sorted(committed) == ["settings.css", "stamp.txt"]  # the agent's work only
    assert (check / "package.json").read_text(encoding="utf-8") == '{"name": "widgets"}\n'
    left_out = next(line for line in logs(poster) if line.startswith("Left out what setup"))
    for path in ("vendor/", "notes.txt", "package.json"):
        assert path in left_out


async def test_an_output_directory_swapped_by_the_agent_is_replaced(
    tmp_path: Path, origin: Path
) -> None:
    def sabotage(repo: Path) -> None:
        edit_settings(repo)
        (repo.parent / ".nextix-out").write_text("not a directory", encoding="utf-8")

    query = ScriptedQuery(result_message(), edit=sabotage)
    instance, _, _, _, _ = build(tmp_path, origin, query, project=project_env(test="npm test"))
    assert await instance.run() == EXIT_OK
    assert read_manifest(tmp_path)["artifacts"][0]["kind"] == "test_report"
    assert read_result(tmp_path)["commits"] == 1
    assert (tmp_path / "work" / ".nextix-out" / "branch.bundle").is_file()


@pytest.mark.parametrize("plant", ["file", "directory", "nothing"])
async def test_the_bundle_holds_the_checked_commit_whatever_the_tests_did(
    tmp_path: Path, origin: Path, plant: str
) -> None:
    out = tmp_path / "work" / ".nextix-out"

    def tamper(repo: Path) -> None:
        # The repo's test code runs as the agent's user: it moves the branch to a commit
        # that was never checked, and may replace the bundle.
        (repo / "unchecked.txt").write_text(f"{CLAUDE_TOKEN}\n", encoding="utf-8")
        seed_commit(repo, "sneaked in")
        bundle = out / "branch.bundle"
        if plant == "file":
            bundle.write_bytes(b"not a bundle")
        elif plant == "directory":
            bundle.unlink()
            (bundle / "x").mkdir(parents=True)

    sweeps: list[str] = []

    def sweep() -> list[int]:
        sweeps.append("swept")
        return [4242]

    shell = FakeShell(effects={"npm test": tamper})
    query = ScriptedQuery(result_message(), edit=edit_settings)
    instance, poster, _, _, _ = build(
        tmp_path, origin, query, project=project_env(test="npm test"), shell=shell, sweep=sweep
    )
    assert await instance.run() == EXIT_OK
    result = read_result(tmp_path)
    assert (result["status"], result["commits"]) == ("succeeded", 1)
    assert len(sweeps) == 2  # after the agent, and after the tests
    lines = logs(poster)
    assert "Stopped 1 process(es) the tests or the app left running." in lines
    rebuilt = "The bundle was changed after it was written; bundling the checked commit again."
    assert (rebuilt in lines) == (plant != "nothing")

    check = tmp_path / "check"
    bundle = out / "branch.bundle"
    git("clone", "--quiet", "--branch", "nextix/issue-7", str(bundle), str(check), cwd=tmp_path)
    assert (check / "settings.css").exists()
    assert not (check / "unchecked.txt").exists()
    assert git("rev-list", "--count", "HEAD", cwd=check).strip() == "2"  # init + the agent's


async def test_the_runners_git_runs_no_hooks_and_never_sees_the_claude_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    git("init", "--quiet", "-b", "main", str(repo), cwd=tmp_path)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    seed_commit(repo, "init")
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text('#!/bin/sh\necho ran >> "$(git rev-parse --git-dir)/hook-ran"\n', "utf-8")
    hook.chmod(0o755)
    marker = repo / ".git" / "hook-ran"

    git("checkout", "--quiet", "-b", "plain", cwd=repo)  # the hook works...
    assert marker.exists()
    marker.unlink()
    done = await runner.run_git(["checkout", "--quiet", "-b", "runner"], cwd=repo)
    assert done.code == 0, done.err
    assert not marker.exists()  # ... but never for the runner

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", CLAUDE_TOKEN)
    monkeypatch.setenv("NEXTIX_PROBE_VAR", "visible")
    done = await runner.run_git(["-c", "alias.show-env=!env", "show-env"], cwd=repo)
    assert done.code == 0, done.err
    assert "NEXTIX_PROBE_VAR=visible" in done.out
    assert CLAUDE_TOKEN not in done.out
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in done.out


# --------------------------------------------------------------------------- the process sweep


def test_the_sweep_kills_only_the_agents_leftover_processes(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    for pid, uid in ((1, 1000), (7, 1000), (8, 1000), (9, 0), (10, 1000)):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "status").write_text(
            f"Name:\tx\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n", encoding="utf-8"
        )
    (proc / "self").mkdir()
    (proc / "11").mkdir()  # exited meanwhile: no status file
    killed: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        if pid == 10:
            raise ProcessLookupError
        killed.append((pid, sig))

    result = sweep_processes(keep={1, 7}, uid=1000, proc_root=proc, kill=kill, sig=9)
    assert result == [8]
    assert killed == [(8, 9)]


def test_the_sweep_is_off_outside_the_sandbox() -> None:
    assert runner.sandbox_sweeper({}) is None


# --------------------------------------------------------------------------- real processes


def pid_alive(pid: int) -> bool:
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except OSError:
        return False
    return "\nState:\tZ" not in status  # a zombie is as good as gone


async def wait_dead(pid: int, within_s: float = 5.0) -> bool:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


def pid_in(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text else None


async def read_pid(path: Path) -> int:
    for _ in range(200):
        pid = pid_in(path)
        if pid is not None:
            return pid
        await asyncio.sleep(0.02)
    raise AssertionError(f"{path} was never written")


def real_env() -> dict[str, str]:
    return command_env(dict(os.environ), Redactor())


@posix_only
async def test_a_command_captures_output_and_exit_code(tmp_path: Path) -> None:
    result = await ProcessShell().run(
        "echo out; echo err >&2; exit 3",
        cwd=tmp_path,
        env=real_env(),
        timeout_s=30,
        keep_bytes=1000,
    )
    assert (result.exit_code, result.timed_out, result.truncated) == (3, False, False)
    assert result.output.decode().split() == ["out", "err"]


@posix_only
async def test_a_command_that_runs_too_long_is_killed_with_its_children(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    started = time.monotonic()
    result = await ProcessShell(kill_grace_s=1).run(
        f"sleep 300 & echo $! > {pidfile}; sleep 300",
        cwd=tmp_path,
        env=real_env(),
        timeout_s=1,
        keep_bytes=1000,
    )
    assert time.monotonic() - started < 15
    assert (result.exit_code, result.timed_out) == (124, True)
    assert await wait_dead(await read_pid(pidfile))


@posix_only
async def test_what_a_command_leaves_running_is_stopped(tmp_path: Path) -> None:
    # The background sleep keeps the output pipe open; without the group kill, reading
    # the output would wait five minutes.
    pidfile = tmp_path / "child.pid"
    started = time.monotonic()
    result = await ProcessShell(kill_grace_s=1).run(
        f"sleep 300 & echo $! > {pidfile}; echo done",
        cwd=tmp_path,
        env=real_env(),
        timeout_s=60,
        keep_bytes=1000,
    )
    assert time.monotonic() - started < 15
    assert (result.exit_code, result.output) == (0, b"done\n")
    assert await wait_dead(await read_pid(pidfile))


@posix_only
async def test_a_process_that_escapes_the_group_cannot_hang_the_runner(tmp_path: Path) -> None:
    # setsid leaves the process group (so the group kill misses it) but keeps the pipe.
    pidfile = tmp_path / "escaped.pid"
    started = time.monotonic()
    try:
        result = await ProcessShell(kill_grace_s=1).run(
            f"setsid sleep 300 & echo $! > {pidfile}; echo done",
            cwd=tmp_path,
            env=real_env(),
            timeout_s=60,
            keep_bytes=1000,
        )
        assert time.monotonic() - started < 15
        assert (result.exit_code, result.output) == (0, b"done\n")
    finally:
        escaped = pid_in(pidfile)
        if escaped is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(escaped, signal.SIGKILL)


@posix_only
async def test_stopping_the_app_kills_its_whole_process_group(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    service = await ProcessShell(kill_grace_s=1).start(
        f"sleep 300 & echo $! > {pidfile}; sleep 300", cwd=tmp_path, env=real_env(), keep_bytes=1000
    )
    child = await read_pid(pidfile)
    before_stop = service.returncode
    assert before_stop is None
    await service.stop()
    assert service.returncode is not None
    assert await wait_dead(child)
    assert await wait_dead(service.pid)


@posix_only
async def test_an_app_ignoring_sigterm_is_killed(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    service = await ProcessShell(kill_grace_s=0.5).start(
        f"trap '' TERM; sleep 300 & echo $! > {pidfile}; wait",
        cwd=tmp_path,
        env=real_env(),
        keep_bytes=1000,
    )
    child = await read_pid(pidfile)
    started = time.monotonic()
    await service.stop()
    assert time.monotonic() - started < 10
    assert await wait_dead(child)


@posix_only
async def test_commands_do_not_see_the_runners_secrets(tmp_path: Path) -> None:
    base = {**os.environ, **environ_with_secrets(), "PATH": os.environ.get("PATH", "/usr/bin")}
    env = command_env(base, Redactor([CLONE_TOKEN, SECRET, CLAUDE_TOKEN]))
    result = await ProcessShell().run(
        "env", cwd=tmp_path, env=env, timeout_s=30, keep_bytes=100_000
    )
    printed = result.output.decode()
    for secret in (CLONE_TOKEN, SECRET, CLAUDE_TOKEN, "GITHUB_TOKEN", "NEXTIX_CALLBACK_SECRET"):
        assert secret not in printed
    assert "CI=true" in printed


# --------------------------------------------------------------------------- the launcher


async def test_the_browser_starts_without_the_runners_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import playwright.async_api

    seen: dict[str, Any] = {}

    class FakeChromium:
        async def launch(self, **kwargs: Any) -> Any:
            seen["launch"] = kwargs
            return FakeChromiumBrowser()

    class FakeChromiumBrowser:
        async def close(self) -> None:
            seen["closed"] = True

    class FakePlaywright:
        chromium = FakeChromium()

        async def stop(self) -> None:
            seen["stopped"] = True

    class FakeManager:
        async def start(self) -> FakePlaywright:
            seen["driver_env"] = dict(os.environ)  # what the driver process would copy
            return FakePlaywright()

    monkeypatch.setattr(playwright.async_api, "async_playwright", FakeManager)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", CLAUDE_TOKEN)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    browser_env = {"PATH": "/usr/bin", "CI": "true"}
    async with runner.PlaywrightLauncher("/ms-playwright")(browser_env):
        pass

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in seen["driver_env"]
    assert seen["driver_env"]["PLAYWRIGHT_BROWSERS_PATH"] == "/ms-playwright"
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == CLAUDE_TOKEN  # restored afterwards
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ
    launch = seen["launch"]
    assert (launch["headless"], launch["chromium_sandbox"]) == (True, False)
    assert launch["env"] == browser_env
    assert "--disable-dev-shm-usage" in launch["args"]
    assert seen["closed"] and seen["stopped"]


# --------------------------------------------------------------------------- real Chromium


def chromium_installed() -> bool:
    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    return sys.platform == "linux" and bool(browsers) and Path(browsers).is_dir()


def write_page(directory: Path) -> None:
    (directory / "index.html").write_text(
        "<!doctype html><title>t</title><h1 style='animation: spin 1s infinite'>Hello</h1>"
        "<style>@keyframes spin { to { transform: rotate(360deg); } }</style>",
        encoding="utf-8",
    )
    (directory / "a b.html").write_text("<p>a path with a space</p>", encoding="utf-8")


@pytest.mark.skipif(not chromium_installed(), reason="needs the sandbox image's Chromium")
async def test_real_chromium_takes_viewport_sized_screenshots(tmp_path: Path) -> None:
    write_page(tmp_path)
    app = await ProcessShell().start(
        f"exec {sys.executable} -m http.server 8765 --bind 127.0.0.1",
        cwd=tmp_path,
        env=real_env(),
        keep_bytes=10_000,
    )
    try:
        probe = runner.HttpProbe()
        for _ in range(100):
            if await probe.status("http://127.0.0.1:8765/") is not None:
                break
            await asyncio.sleep(0.1)
        assert await probe.listening(8765)
        assert await probe.status("http://127.0.0.1:8765/a b.html") == 200
        launcher = runner.PlaywrightLauncher(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
        async with launcher(real_env()) as browser:
            first = await browser.screenshot("http://127.0.0.1:8765/", Viewport(640, 360))
            second = await browser.screenshot("http://127.0.0.1:8765/", Viewport(640, 360))
            spaced = await browser.screenshot("http://127.0.0.1:8765/a b.html", Viewport(320, 240))
            assert png_size(spaced) == (320, 240)
            with pytest.raises(CaptureError, match="HTTP 404"):
                await browser.screenshot("http://127.0.0.1:8765/nope", Viewport(640, 360))
    finally:
        await app.stop()
    assert not await runner.HttpProbe().listening(8765)
    assert png_size(first) == (640, 360)
    assert pixel_diff(first, second).diff_pixels == 0  # animations are disabled


# --------------------------------------------------------------------------- secrets file


def test_secrets_arrive_in_a_file_that_is_deleted(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps(
            {
                "GITHUB_TOKEN": "ghs_x",
                "NEXTIX_CALLBACK_SECRET": "s3cret",
                "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-y",
                "PATH": "/evil",  # not a secret name: ignored
                "ANTHROPIC_API_KEY": 5,  # not a string: ignored
            }
        ),
        encoding="utf-8",
    )
    environ: dict[str, str] = {"PATH": "/usr/bin"}
    assert runner.load_secrets_file(environ, secrets) is True
    assert not secrets.exists()
    assert environ == {
        "PATH": "/usr/bin",
        "GITHUB_TOKEN": "ghs_x",
        "NEXTIX_CALLBACK_SECRET": "s3cret",
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-y",
    }


def test_no_secrets_file_is_fine(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    assert runner.load_secrets_file(environ, tmp_path / "missing.json") is False
    assert environ == {}
