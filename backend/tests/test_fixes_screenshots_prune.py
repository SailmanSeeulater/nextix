"""Daily pruning of the nextix/screenshots branch: finished tickets' images are removed."""

import json

import httpx
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from nextix.db.models import Repo, Ticket
from nextix.github.client import GitHubClient
from nextix.housekeeping import prune_pr_screenshots, stale_screenshot_paths

REPO = "/repos/acme/widgets"
BRANCH = "nextix/screenshots"
TREE = [
    "issue-12/attempt-1/root-before.png",
    "issue-12/attempt-1/root-after.png",
    "issue-12/attempt-1/root-diff.png",
    "issue-12/attempt-2/root-after.png",
    "issue-13/attempt-1/root-before.png",
    "issue-14/attempt-1/root-before.png",
    "README.md",
]


def test_only_finished_issues_folders_are_stale() -> None:
    assert stale_screenshot_paths(TREE, {12, 14}) == [
        "issue-12/attempt-1/root-before.png",
        "issue-12/attempt-1/root-after.png",
        "issue-12/attempt-1/root-diff.png",
        "issue-12/attempt-2/root-after.png",
        "issue-14/attempt-1/root-before.png",
    ]
    assert stale_screenshot_paths(TREE, set()) == []


async def seed(session: AsyncSession) -> Repo:
    repo = Repo(owner="acme", name="widgets", installation_id=777, default_branch="main")
    session.add(repo)
    await session.flush()
    session.add_all(
        [
            # merged: stale
            Ticket(
                repo_id=repo.id,
                issue_number=12,
                title="a",
                issue_state="closed",
                labels=["nextix"],
                pr_number=40,
                pr_state="merged",
            ),
            # still in review: keep
            Ticket(
                repo_id=repo.id,
                issue_number=13,
                title="b",
                issue_state="open",
                labels=["nextix"],
                pr_number=41,
                pr_state="open",
            ),
            # issue closed without a PR: stale
            Ticket(
                repo_id=repo.id, issue_number=14, title="c", issue_state="closed", labels=["nextix"]
            ),
        ]
    )
    await session.commit()
    return repo


def git_mocks(respx_mock: respx.MockRouter, paths: list[str]) -> dict[str, respx.Route]:
    return {
        "list": respx_mock.get(f"{REPO}/git/trees/{BRANCH}").respond(
            200, json={"tree": [{"path": p, "type": "blob"} for p in paths]}
        ),
        "ref": respx_mock.get(f"{REPO}/git/ref/heads/{BRANCH}").respond(
            200, json={"object": {"sha": "oldhead"}}
        ),
        "head": respx_mock.get(f"{REPO}/git/commits/oldhead").respond(
            200, json={"tree": {"sha": "oldtree"}}
        ),
        "blob": respx_mock.post(f"{REPO}/git/blobs").respond(201, json={"sha": "blob"}),
        "tree": respx_mock.post(f"{REPO}/git/trees").respond(201, json={"sha": "newtree"}),
        "commit": respx_mock.post(f"{REPO}/git/commits").respond(201, json={"sha": "newcommit"}),
        "update": respx_mock.patch(f"{REPO}/git/refs/heads/{BRANCH}").respond(
            200, json={"object": {"sha": "newcommit"}}
        ),
    }


async def test_finished_tickets_images_are_deleted_in_one_commit(
    session: AsyncSession, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    await seed(session)
    routes = git_mocks(respx_mock, TREE)
    assert await prune_pr_screenshots(session, gh) == 5

    tree = json.loads(routes["tree"].calls[0].request.content)
    assert tree["base_tree"] == "oldtree"
    assert all(e["sha"] is None for e in tree["tree"])  # deletions only, no blobs uploaded
    assert sorted(e["path"] for e in tree["tree"]) == sorted(stale_screenshot_paths(TREE, {12, 14}))
    assert routes["blob"].call_count == 0
    commit = json.loads(routes["commit"].calls[0].request.content)
    assert commit["parents"] == ["oldhead"] and "#12, #14" in commit["message"]
    assert json.loads(routes["update"].calls[0].request.content)["force"] is False


async def test_nothing_to_prune_makes_no_commit(
    session: AsyncSession, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    await seed(session)
    routes = git_mocks(respx_mock, ["issue-13/attempt-1/root-before.png"])
    assert await prune_pr_screenshots(session, gh) == 0
    assert routes["commit"].call_count == 0


async def test_a_repo_without_the_branch_is_skipped(
    session: AsyncSession, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    await seed(session)
    respx_mock.get(f"{REPO}/git/trees/{BRANCH}").respond(404, json={})
    assert await prune_pr_screenshots(session, gh) == 0


async def test_a_github_error_on_one_repo_does_not_stop_the_sweep(
    session: AsyncSession, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    await seed(session)
    other = Repo(owner="acme", name="gadgets", installation_id=777, default_branch="main")
    session.add(other)
    await session.commit()
    respx_mock.get("/repos/acme/gadgets/git/trees/nextix/screenshots").mock(
        side_effect=httpx.ConnectError("boom")
    )
    routes = git_mocks(respx_mock, TREE)
    # gadgets fails, widgets is still pruned.
    assert await prune_pr_screenshots(session, gh) == 5
    assert routes["commit"].call_count == 1
