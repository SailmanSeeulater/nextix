"""Backfill with mocked GitHub REST responses."""

from typing import Any

import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.db.models import Repo, Ticket
from nextix.github.client import GitHubClient
from nextix.sync import sync_repo
from tests.conftest import API, load_fixture


def _issue(number: int, labels: list[str], state: str = "open", **extra: Any) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"issue {number}",
        "body": "",
        "state": state,
        "labels": [{"name": n} for n in labels],
        **extra,
    }


def _pr(
    number: int, ref: str, *, state: str = "open", merged_at: str | None = None
) -> dict[str, Any]:
    head_repo = {"id": 5001, "name": "widgets", "full_name": "acme/widgets"}
    return {
        "number": number,
        "state": state,
        "merged_at": merged_at,
        "head": {"ref": ref, "repo": head_repo},
        "base": {"ref": "main", "repo": head_repo},
    }


def _mock_github(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("/repos/acme/widgets/installation").respond(200, json={"id": 777})
    widgets = load_fixture("issues_labeled.json")["repository"]
    respx_mock.get("/installation/repositories").respond(
        200, json={"total_count": 1, "repositories": [widgets]}
    )
    respx_mock.get("/repos/acme/widgets").respond(
        200, json=load_fixture("issues_labeled.json")["repository"]
    )
    # Two pages, to exercise Link-header pagination. Page 1 also contains a PR,
    # which the issues endpoint returns and which must be skipped.
    respx_mock.get("/repos/acme/widgets/issues", params={"page": "2"}).respond(
        200, json=[_issue(3, ["nextix"], state="closed")]
    )
    respx_mock.get("/repos/acme/widgets/issues").respond(
        200,
        json=[
            _issue(1, ["nextix", "ui"]),
            _issue(2, ["nextix"]),
            _issue(9, ["nextix"], pull_request={"url": "x"}),
        ],
        headers={"Link": f'<{API}/repos/acme/widgets/issues?labels=nextix&page=2>; rel="next"'},
    )
    respx_mock.get("/repos/acme/widgets/pulls").respond(
        200,
        json=[
            _pr(10, "nextix/issue-1"),
            _pr(11, "nextix/issue-3", state="closed", merged_at="2026-09-01T00:00:00Z"),
            _pr(12, "some-feature"),
        ],
    )


async def test_backfills_issues_and_prs(
    session: AsyncSession, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    _mock_github(respx_mock)
    report, changed = await sync_repo(session, gh, "acme/widgets")
    await session.commit()

    assert (report.issues, report.prs_linked) == (3, 2)
    assert report.installation_repos == "1 repos active, 0 disabled, 0 removed"
    repo = await session.scalar(select(Repo))
    assert repo is not None and repo.installation_id == 777
    tickets = {t.issue_number: t for t in await session.scalars(select(Ticket))}
    assert set(tickets) == {1, 2, 3}  # the PR (#9) was filtered out
    assert (tickets[1].pr_number, tickets[1].pr_state) == (10, "open")
    assert (tickets[3].issue_state, tickets[3].pr_state) == ("closed", "merged")
    assert tickets[2].pr_number is None
    assert len(changed) == 3


async def test_resync_corrects_drift(
    session: AsyncSession, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    _mock_github(respx_mock)
    await sync_repo(session, gh, "acme/widgets")
    await session.commit()

    # While we weren't listening: #2 lost its label, #1 was deleted.
    respx_mock.get("/repos/acme/widgets/issues", params={"page": "2"}).respond(200, json=[])
    respx_mock.get("/repos/acme/widgets/issues").respond(
        200, json=[_issue(3, ["nextix"], state="closed")]
    )
    respx_mock.get("/repos/acme/widgets/issues/2").respond(200, json=_issue(2, ["bug"]))
    respx_mock.get("/repos/acme/widgets/issues/1").respond(404, json={"message": "Not Found"})

    report, _ = await sync_repo(session, gh, "acme/widgets")
    await session.commit()

    assert report.rechecked == 2
    tickets = {
        t.issue_number: t
        for t in await session.scalars(select(Ticket).execution_options(populate_existing=True))
    }
    assert tickets[2].labels == ["bug"]
    assert tickets[1].issue_state == "deleted"


async def test_sync_retires_repos_the_installation_lost(
    session: AsyncSession, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    """Rows left over from an earlier, wider installation are cleaned up."""
    session.add(Repo(owner="acme", name="old-thing", installation_id=777, default_branch="main"))
    await session.commit()
    _mock_github(respx_mock)

    report, _ = await sync_repo(session, gh, "acme/widgets")
    await session.commit()

    assert report.installation_repos == "1 repos active, 0 disabled, 1 removed"
    names = [r.name for r in await session.scalars(select(Repo))]
    assert names == ["widgets"]
