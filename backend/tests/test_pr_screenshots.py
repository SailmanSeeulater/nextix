"""Before/after screenshots posted on the PR, with images on the nextix/screenshots branch."""

import base64
import json
import uuid
from pathlib import Path
from typing import Any

import respx

from nextix.db.models import Artifact, Repo, Run, Ticket
from nextix.github.client import GitHubClient
from nextix.review.pr_screenshots import SCREENSHOTS_BRANCH, publish_screenshots, route_slug

REPO = "/repos/acme/widgets"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x02" * 16


def setup(tmp_path: Path) -> tuple[Repo, Ticket, Run]:
    repo = Repo(owner="acme", name="widgets", installation_id=777, default_branch="main")
    ticket = Ticket(id=uuid.uuid4(), issue_number=11, title="t", pr_number=12)
    run = Run(id=uuid.uuid4(), attempt=3)
    return repo, ticket, run


def shot(tmp_path: Path, kind: str, label: str, **meta: Any) -> Artifact:
    name = f"{uuid.uuid4()}.png"
    (tmp_path / name).write_bytes(PNG + kind.encode())
    return Artifact(kind=f"screenshot_{kind}", label=label, path=name, meta=meta)


def route(tmp_path: Path, label: str, pixels: int) -> list[Artifact]:
    return [
        shot(tmp_path, "before", label, baseline="nextix/issue-11 before this change"),
        shot(tmp_path, "after", label),
        shot(tmp_path, "diff", label, diff_pixels=pixels, diff_pct=round(pixels / 10240, 2)),
    ]


def git_mocks(respx_mock: respx.MockRouter, *, head: str | None) -> dict[str, respx.Route]:
    blobs = iter(range(100))
    ref = f"{REPO}/git/ref/heads/nextix/screenshots"
    return {
        "ref": respx_mock.get(ref).respond(200, json={"object": {"sha": head}})
        if head
        else respx_mock.get(ref).respond(404, json={}),
        "head": respx_mock.get(f"{REPO}/git/commits/{head}").respond(
            200, json={"tree": {"sha": "basetree"}}
        ),
        "blob": respx_mock.post(f"{REPO}/git/blobs").mock(
            side_effect=lambda _r: respx.MockResponse(201, json={"sha": f"blob{next(blobs)}"})
        ),
        "tree": respx_mock.post(f"{REPO}/git/trees").respond(201, json={"sha": "newtree"}),
        "commit": respx_mock.post(f"{REPO}/git/commits").respond(201, json={"sha": "newcommit"}),
        "update": respx_mock.patch(f"{REPO}/git/refs/heads/nextix/screenshots").respond(
            200, json={}
        ),
        "create": respx_mock.post(f"{REPO}/git/refs").respond(201, json={}),
        "comment": respx_mock.post(f"{REPO}/issues/12/comments").respond(201, json={}),
    }


async def test_changed_routes_are_stored_and_shown_on_the_pr(
    tmp_path: Path, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    repo, ticket, run = setup(tmp_path)
    routes = git_mocks(respx_mock, head="oldhead")
    shots = route(tmp_path, "/", 1792) + route(tmp_path, "/about", 0)
    assert await publish_screenshots(gh, repo, ticket, run, shots, tmp_path) is True

    # Only the changed route's three images are committed, on top of the branch head.
    blobs = [json.loads(c.request.content) for c in routes["blob"].calls]
    assert len(blobs) == 3 and base64.b64decode(blobs[0]["content"]).startswith(b"\x89PNG")
    tree = json.loads(routes["tree"].calls[0].request.content)
    assert tree["base_tree"] == "basetree"
    assert sorted(e["path"] for e in tree["tree"]) == [
        "issue-11/attempt-3/root-after.png",
        "issue-11/attempt-3/root-before.png",
        "issue-11/attempt-3/root-diff.png",
    ]
    assert json.loads(routes["commit"].calls[0].request.content)["parents"] == ["oldhead"]
    assert json.loads(routes["update"].calls[0].request.content) == {
        "sha": "newcommit",
        "force": False,
    }

    body = json.loads(routes["comment"].calls[0].request.content)["body"]
    assert "Compared with **nextix/issue-11 before this change**" in body
    assert "#### `/` · 0.17% of pixels changed" in body
    url = "https://github.com/acme/widgets/blob/nextix/screenshots/issue-11/attempt-3/"
    assert f'<img src="{url}root-before.png?raw=true"' in body
    assert f'<img src="{url}root-after.png?raw=true"' in body
    assert "No visible change: `/about`" in body


async def test_the_branch_is_created_as_an_orphan_the_first_time(
    tmp_path: Path, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    repo, ticket, run = setup(tmp_path)
    routes = git_mocks(respx_mock, head=None)
    await publish_screenshots(gh, repo, ticket, run, route(tmp_path, "/", 50), tmp_path)
    assert "base_tree" not in json.loads(routes["tree"].calls[0].request.content)
    assert json.loads(routes["commit"].calls[0].request.content)["parents"] == []
    assert json.loads(routes["create"].calls[0].request.content)["ref"] == (
        f"refs/heads/{SCREENSHOTS_BRANCH}"
    )


async def test_no_visible_change_posts_nothing(
    tmp_path: Path, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    repo, ticket, run = setup(tmp_path)
    routes = git_mocks(respx_mock, head="oldhead")
    posted = await publish_screenshots(gh, repo, ticket, run, route(tmp_path, "/", 0), tmp_path)
    assert posted is False and not routes["blob"].called and not routes["comment"].called


async def test_a_github_failure_never_breaks_the_run(
    tmp_path: Path, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    repo, ticket, run = setup(tmp_path)
    routes = git_mocks(respx_mock, head="oldhead")
    routes["tree"].respond(500)
    assert (
        await publish_screenshots(gh, repo, ticket, run, route(tmp_path, "/", 9), tmp_path) is False
    )
    assert not routes["comment"].called


def test_route_slugs() -> None:
    assert [route_slug(r) for r in ("/", "/about", "/a b/C?x=1")] == ["root", "about", "a-b-c-x-1"]
