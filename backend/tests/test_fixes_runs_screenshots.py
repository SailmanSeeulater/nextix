"""Regression tests for PR screenshots: unreadable files never raise, a route is committed
whole or not at all, and colliding route slugs never overwrite each other's images."""

import json
from pathlib import Path

import pytest
import respx

from nextix.db.models import Artifact
from nextix.github.client import GitHubClient
from nextix.review import pr_screenshots
from nextix.review.pr_screenshots import publish_screenshots, unique_slug
from tests.test_pr_screenshots import git_mocks, route, setup


def committed(routes: dict[str, respx.Route]) -> list[str]:
    return sorted(e["path"] for e in json.loads(routes["tree"].calls[0].request.content)["tree"])


async def test_an_unreadable_image_never_raises_and_skips_its_route(
    tmp_path: Path,
    gh: GitHubClient,
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, ticket, run = setup(tmp_path)
    routes = git_mocks(respx_mock, head="oldhead")
    broken = route(tmp_path, "/broken", 40)
    real = pr_screenshots.resolve_path

    def resolve(artifact_dir: Path, artifact: Artifact) -> Path | None:
        if artifact.label == "/broken" and artifact.kind == "screenshot_diff":
            return tmp_path / "missing" / "gone.png"  # read_bytes raises FileNotFoundError
        return real(artifact_dir, artifact)

    monkeypatch.setattr(pr_screenshots, "resolve_path", resolve)
    shots = route(tmp_path, "/", 9) + broken
    assert await publish_screenshots(gh, repo, ticket, run, shots, tmp_path) is True
    # The broken route's before and after were read, but only whole routes are committed.
    assert committed(routes) == [
        "issue-11/attempt-3/root-after.png",
        "issue-11/attempt-3/root-before.png",
        "issue-11/attempt-3/root-diff.png",
    ]
    body = json.loads(routes["comment"].calls[0].request.content)["body"]
    assert "/broken" not in body


async def test_a_route_missing_an_image_on_disk_is_left_out(
    tmp_path: Path, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    repo, ticket, run = setup(tmp_path)
    routes = git_mocks(respx_mock, head="oldhead")
    partial = route(tmp_path, "/partial", 40)
    (tmp_path / partial[1].path).unlink()  # the after image is gone
    shots = route(tmp_path, "/", 9) + partial
    assert await publish_screenshots(gh, repo, ticket, run, shots, tmp_path) is True
    assert not any("partial" in path for path in committed(routes))


async def test_colliding_route_slugs_get_their_own_images(
    tmp_path: Path, gh: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    repo, ticket, run = setup(tmp_path)
    routes = git_mocks(respx_mock, head="oldhead")
    shots = route(tmp_path, "/a-b", 9) + route(tmp_path, "/a/b", 9)
    assert await publish_screenshots(gh, repo, ticket, run, shots, tmp_path) is True
    paths = committed(routes)
    assert len(paths) == 6 and len(routes["blob"].calls) == 6
    body = json.loads(routes["comment"].calls[0].request.content)["body"]
    assert "a-b-before.png" in body and "a-b-2-before.png" in body


def test_unique_slugs() -> None:
    taken: set[str] = set()
    assert [unique_slug(label, taken) for label in ("/a-b", "/a/b", "/A B", "/")] == [
        "a-b",
        "a-b-2",
        "a-b-3",
        "root",
    ]
