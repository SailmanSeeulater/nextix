"""CLI behavior against a mocked API. No network, config in a temp dir."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from nextix_cli import config
from nextix_cli.main import app

API = "http://nextix.test"
runner = CliRunner()

CARD: dict[str, Any] = {
    "id": "t1",
    "repo": "acme/widgets",
    "issue_number": 7,
    "title": "Add a dark mode toggle",
    "labels": ["nextix"],
    "issue_state": "open",
    "issue_url": "https://github.com/acme/widgets/issues/7",
    "pr_number": None,
    "pr_state": None,
    "pr_url": None,
    "column": "todo",
    "latest_run": None,
    "created_via": "cli",
    "updated_at": "2026-09-25T10:00:00Z",
}


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("NEXTIX_CONFIG_DIR", str(tmp_path))
    for var in ("NEXTIX_API_URL", "NEXTIX_API_TOKEN", "NEXTIX_REPO"):
        monkeypatch.delenv(var, raising=False)
    yield tmp_path


@pytest.fixture
def signed_in() -> None:
    config.save(config.CliConfig(api_url=API, token="tok", default_repo="acme/widgets"))


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=API, assert_all_called=False) as mock:
        yield mock


# ------------------------------------------------------------------ login


def test_login_verifies_and_saves(api: respx.MockRouter, isolated_config: Path) -> None:
    route = api.get("/api/tickets").respond(200, json=[])
    result = runner.invoke(
        app, ["login", "--api-url", API, "--repo", "acme/widgets"], input="s3cret-value\n"
    )
    assert result.exit_code == 0, result.output
    assert route.calls[0].request.headers["Authorization"] == "Bearer s3cret-value"
    cfg = config.load()
    assert (cfg.api_url, cfg.token, cfg.default_repo) == (API, "s3cret-value", "acme/widgets")
    assert "s3cret-value" not in result.output  # hidden input is never echoed


def test_login_rejects_bad_token(api: respx.MockRouter, isolated_config: Path) -> None:
    api.get("/api/tickets").respond(401, json={"detail": "invalid"})
    result = runner.invoke(app, ["login", "--api-url", API, "--repo", ""], input="bad\n")
    assert result.exit_code == 1
    assert "rejected your token" in result.output
    assert not config.config_path().exists()


def test_commands_require_login() -> None:
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 1
    assert "nextix login" in result.output


# ------------------------------------------------------------------ new


@pytest.mark.usefixtures("signed_in")
def test_new_creates_ticket(api: respx.MockRouter) -> None:
    route = api.post("/api/tickets").respond(
        201,
        json={
            "ticket": CARD,
            "issue_url": CARD["issue_url"],
            "board_url": "http://localhost:3001/",
            "needs_input": False,
            "clarifying_question": None,
        },
    )
    result = runner.invoke(app, ["new", "add a dark mode toggle", "-l", "ui", "-l", "p1"])
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls[0].request.content)
    assert body == {
        "repo": "acme/widgets",
        "prompt": "add a dark mode toggle",
        "labels": ["ui", "p1"],
        "triage": True,
        "created_via": "cli",
    }
    assert "acme/widgets#7" in result.output
    assert CARD["issue_url"] in result.output
    assert "http://localhost:3001/" in result.output


@pytest.mark.usefixtures("signed_in")
def test_new_shows_clarifying_question(api: respx.MockRouter) -> None:
    api.post("/api/tickets").respond(
        201,
        json={
            "ticket": {**CARD, "column": "needs_input"},
            "issue_url": CARD["issue_url"],
            "board_url": "http://localhost:3001/",
            "needs_input": True,
            "clarifying_question": "Which page?",
        },
    )
    result = runner.invoke(app, ["new", "make it better", "--repo", "acme/other"])
    assert result.exit_code == 0, result.output
    assert "Needs input" in result.output and "Which page?" in result.output


@pytest.mark.usefixtures("signed_in")
def test_new_no_triage_flag(api: respx.MockRouter) -> None:
    route = api.post("/api/tickets").respond(
        201,
        json={
            "ticket": CARD,
            "issue_url": CARD["issue_url"],
            "board_url": "/",
            "needs_input": False,
            "clarifying_question": None,
        },
    )
    runner.invoke(app, ["new", "Add CONTRIBUTING.md", "--no-triage"])
    assert json.loads(route.calls[0].request.content)["triage"] is False


@pytest.mark.usefixtures("signed_in")
def test_new_reports_api_error_detail(api: respx.MockRouter) -> None:
    api.post("/api/tickets").respond(502, json={"detail": "Triage failed: model declined."})
    result = runner.invoke(app, ["new", "something"])
    assert result.exit_code == 1
    assert "Triage failed: model declined." in result.output


@pytest.mark.usefixtures("signed_in")
def test_new_reports_validation_errors(api: respx.MockRouter) -> None:
    api.post("/api/tickets").respond(
        422, json={"detail": [{"loc": ["body", "repo"], "msg": "String should match pattern"}]}
    )
    result = runner.invoke(app, ["new", "something", "--repo", "bad repo"])
    assert result.exit_code == 1
    assert "Invalid repo" in result.output


@pytest.mark.usefixtures("signed_in")
def test_unreachable_api_is_explained(api: respx.MockRouter) -> None:
    api.post("/api/tickets").mock(side_effect=httpx.ConnectError("refused"))
    result = runner.invoke(app, ["new", "something"])
    assert result.exit_code == 1
    assert "Can't reach the nexTix API" in result.output


# ------------------------------------------------------------------ ls / open


@pytest.mark.usefixtures("signed_in")
def test_ls_lists_by_column(api: respx.MockRouter) -> None:
    route = api.get("/api/tickets").respond(
        200,
        json=[
            {**CARD, "issue_number": 9, "title": "Later", "column": "done"},
            {**CARD, "pr_number": 3, "column": "in_review"},
        ],
    )
    result = runner.invoke(app, ["ls", "--column", "in_review"])
    assert result.exit_code == 0, result.output
    assert route.calls[0].request.url.params["column"] == "in_review"
    assert route.calls[0].request.url.params["repo"] == "acme/widgets"
    assert result.output.index("In Review") < result.output.index("Done")
    assert "#3" in result.output


@pytest.mark.usefixtures("signed_in")
def test_open_prints_issue_url(api: respx.MockRouter) -> None:
    api.get("/api/tickets").respond(200, json=[CARD])
    result = runner.invoke(app, ["open", "7", "--print"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == CARD["issue_url"]


@pytest.mark.usefixtures("signed_in")
def test_open_errors(api: respx.MockRouter) -> None:
    api.get("/api/tickets").respond(200, json=[CARD])
    assert "No ticket #99" in runner.invoke(app, ["open", "99", "--print"]).output
    assert "no linked pull request" in runner.invoke(app, ["open", "7", "--pr", "--print"]).output


def test_env_overrides_file(monkeypatch: pytest.MonkeyPatch) -> None:
    config.save(config.CliConfig(api_url="http://file", token="file-tok"))
    monkeypatch.setenv("NEXTIX_API_URL", "http://env/")
    monkeypatch.setenv("NEXTIX_API_TOKEN", "env-tok")
    cfg = config.load()
    assert (cfg.api_url, cfg.token) == ("http://env", "env-tok")
