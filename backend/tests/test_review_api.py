"""The review surface over HTTP: artifacts, the PR diff, CI checks (docs/phase4.md)."""

import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import nextix.api.review as review_api
from nextix.api.deps import get_enqueuer, get_github, get_publisher
from nextix.config import Settings, get_settings
from nextix.db.models import Artifact, CheckRun, Repo, Run, Ticket
from nextix.db.session import get_db
from nextix.github.client import GitHubClient
from nextix.main import create_app
from tests.conftest import FakeEnqueuer, FakePublisher

AUTH = {"Authorization": "Bearer test-api-token"}
REPO = "/repos/acme/widgets"
SHA = "abc1234def"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 16


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return get_settings().model_copy(update={"artifact_dir": tmp_path})


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    gh: GitHubClient,
    publisher: FakePublisher,
    enqueuer: FakeEnqueuer,
    settings: Settings,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_github] = lambda: gh
    app.dependency_overrides[get_publisher] = lambda: publisher
    app.dependency_overrides[get_enqueuer] = lambda: enqueuer
    app.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def seed(session: AsyncSession, *, pr: bool = True) -> tuple[Repo, Ticket, Run]:
    repo = Repo(owner="acme", name="widgets", installation_id=777, default_branch="main")
    session.add(repo)
    await session.flush()
    ticket = Ticket(
        repo_id=repo.id,
        issue_number=7,
        title="Dark mode",
        issue_state="open",
        labels=["nextix"],
        pr_number=12 if pr else None,
        pr_state="open" if pr else None,
        pr_head_sha=SHA if pr else None,
    )
    session.add(ticket)
    await session.flush()
    run = Run(
        ticket_id=ticket.id,
        attempt=1,
        status="succeeded",
        branch="nextix/issue-7",
        tests={"command": "npm test", "exit_code": 0, "passed": True, "duration_s": 3.0},
    )
    session.add(run)
    await session.commit()
    return repo, ticket, run


def check(id_: int, name: str, conclusion: str | None, sha: str = SHA) -> dict[str, Any]:
    return {
        "id": id_,
        "name": name,
        "head_sha": sha,
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "html_url": f"https://github.com/acme/widgets/runs/{id_}",
        "app": {"name": "GitHub Actions"},
        "check_suite": {"head_branch": "nextix/issue-7"},
    }


# ------------------------------------------------------------------ artifacts


async def test_artifacts_are_served_with_their_type_and_cached(
    client: httpx.AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    _, _, run = await seed(session)
    artifact = Artifact(run_id=run.id, kind="screenshot_after", label="/", path=f"{run.id}/a.png")
    session.add(artifact)
    await session.commit()
    (settings.artifact_dir / str(run.id)).mkdir()
    (settings.artifact_dir / str(run.id) / "a.png").write_bytes(PNG)

    r = await client.get(f"/api/artifacts/{artifact.id}", headers=AUTH)
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert (await client.get(f"/api/artifacts/{artifact.id}")).status_code == 401
    assert (await client.get(f"/api/artifacts/{uuid.uuid4()}", headers=AUTH)).status_code == 404


async def test_an_artifact_row_pointing_outside_the_directory_is_not_served(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, _, run = await seed(session)
    artifact = Artifact(run_id=run.id, kind="test_report", label="x", path="../../etc/passwd")
    session.add(artifact)
    await session.commit()
    assert (await client.get(f"/api/artifacts/{artifact.id}", headers=AUTH)).status_code == 404


# ------------------------------------------------------------------ diff


async def test_the_pr_diff_comes_from_github(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    _, ticket, _ = await seed(session)
    route = respx_mock.get(f"{REPO}/pulls/12").respond(200, text="diff --git a/x b/x\n+hi\n")
    r = await client.get(f"/api/tickets/{ticket.id}/diff", headers=AUTH)
    assert r.json() == {
        "pr_number": 12,
        "head_sha": SHA,
        "diff": "diff --git a/x b/x\n+hi\n",
        "truncated": False,
    }
    assert route.calls[0].request.headers["accept"] == "application/vnd.github.diff"


async def test_huge_diffs_are_cut_or_reported_too_large(
    client: httpx.AsyncClient,
    session: AsyncSession,
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, ticket, _ = await seed(session)
    monkeypatch.setattr(review_api, "MAX_DIFF_BYTES", 10)
    route = respx_mock.get(f"{REPO}/pulls/12").respond(200, text="x" * 100)
    cut = (await client.get(f"/api/tickets/{ticket.id}/diff", headers=AUTH)).json()
    assert (cut["diff"], cut["truncated"]) == ("x" * 10, True)
    route.respond(406, json={"message": "diff exceeded the maximum number of lines"})
    refused = (await client.get(f"/api/tickets/{ticket.id}/diff", headers=AUTH)).json()
    assert (refused["diff"], refused["truncated"]) == ("", True)
    route.respond(500)
    assert (await client.get(f"/api/tickets/{ticket.id}/diff", headers=AUTH)).status_code == 502


async def test_no_pr_no_diff(client: httpx.AsyncClient, session: AsyncSession) -> None:
    _, ticket, _ = await seed(session, pr=False)
    assert (await client.get(f"/api/tickets/{ticket.id}/diff", headers=AUTH)).status_code == 404


# ------------------------------------------------------------------ checks on the ticket page


async def test_ticket_detail_carries_checks_tests_and_artifacts(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    _, ticket, run = await seed(session)
    session.add(Artifact(run_id=run.id, kind="test_report", label="npm test", path="r.txt"))
    await session.commit()
    route = respx_mock.get(f"{REPO}/commits/{SHA}/check-runs").respond(
        200,
        json={
            "total_count": 3,
            "check_runs": [
                check(1, "build", "failure"),
                check(2, "build", "success"),  # a re-run: the newest wins
                check(3, "lint", None),
            ],
        },
    )
    detail = (await client.get(f"/api/tickets/{ticket.id}", headers=AUTH)).json()
    assert detail["pr_head_sha"] == SHA
    assert detail["checks"]["connected"] is True
    assert [(c["name"], c["conclusion"]) for c in detail["checks"]["runs"]] == [
        ("build", "success"),
        ("lint", None),
    ]
    [run_json] = detail["runs"]
    assert run_json["tests"]["passed"] is True
    assert [a["kind"] for a in run_json["artifacts"]] == ["test_report"]
    assert run_json["artifacts"][0]["url"].startswith("/api/artifacts/")

    await client.get(f"/api/tickets/{ticket.id}", headers=AUTH)
    assert route.call_count == 1  # read from GitHub at most once a minute


async def test_without_the_checks_permission_the_page_says_so(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    _, ticket, _ = await seed(session)
    respx_mock.get(f"{REPO}/commits/{SHA}/check-runs").respond(
        403, json={"message": "Resource not accessible by integration"}
    )
    detail = (await client.get(f"/api/tickets/{ticket.id}", headers=AUTH)).json()
    assert detail["checks"] == {"connected": False, "runs": []}


async def test_failing_tests_mark_the_board_card(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, _, run = await seed(session, pr=False)
    run.tests = {"command": "npm test", "exit_code": 1, "passed": False, "duration_s": 1.0}
    await session.commit()
    [card] = (await client.get("/api/tickets", headers=AUTH)).json()
    assert card["tests_failing"] is True


# ------------------------------------------------------------------ check webhooks


def signed(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()
    return {
        "content": body,
        "headers": {
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
    }


def repo_payload() -> dict[str, Any]:
    return {
        "repository": {
            "id": 1,
            "name": "widgets",
            "full_name": "acme/widgets",
            "owner": {"login": "acme"},
            "default_branch": "main",
        },
        "installation": {"id": 777},
    }


async def test_check_run_webhooks_are_stored_and_announced(
    client: httpx.AsyncClient, session: AsyncSession, publisher: FakePublisher
) -> None:
    _, ticket, _ = await seed(session)
    payload = {**repo_payload(), "action": "completed", "check_run": check(9, "test", "failure")}
    r = await client.post("/api/github/webhook", **signed("check_run", payload))
    assert r.status_code == 200, r.text
    row = await session.get(CheckRun, 9)
    assert row is not None and (row.name, row.conclusion) == ("test", "failure")
    assert any(e == "ticket.updated" and d["id"] == str(ticket.id) for _, e, d in publisher.events)


async def test_a_completed_check_suite_rereads_the_runs(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    await seed(session)
    route = respx_mock.get(f"{REPO}/commits/{SHA}/check-runs").respond(
        200, json={"total_count": 1, "check_runs": [check(5, "e2e", "success")]}
    )
    payload = {**repo_payload(), "action": "completed", "check_suite": {"head_sha": SHA}}
    await client.post("/api/github/webhook", **signed("check_suite", payload))
    assert route.called
    assert (await session.scalars(select(CheckRun.name))).all() == ["e2e"]


async def test_commit_statuses_show_up_as_checks(
    client: httpx.AsyncClient, session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    _, ticket, _ = await seed(session)
    respx_mock.get(f"{REPO}/commits/{SHA}/check-runs").respond(
        200, json={"total_count": 0, "check_runs": []}
    )
    respx_mock.get(f"{REPO}/commits/{SHA}/status").respond(
        200,
        json={
            "state": "failure",
            "statuses": [
                {"context": "vercel", "state": "success", "target_url": "https://v.example"},
                {"context": "ci/circleci", "state": "error", "description": "Build errored"},
                {"context": "coverage", "state": "pending"},
            ],
        },
    )
    detail = (await client.get(f"/api/tickets/{ticket.id}", headers=AUTH)).json()
    got = {c["name"]: (c["status"], c["conclusion"]) for c in detail["checks"]["runs"]}
    assert got == {
        "vercel": ("completed", "success"),
        "ci/circleci": ("completed", "failure"),
        "coverage": ("in_progress", None),
    }
    assert all(c["id"] < 0 and c["app_name"] == "Commit status" for c in detail["checks"]["runs"])
