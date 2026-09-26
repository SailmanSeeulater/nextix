"""Thin typed wrapper over the GitHub REST API calls nexTix needs.

All calls authenticate as an installation except ``get_repo_installation``,
which uses the app JWT to discover which installation covers a repo.
"""

import base64
import binascii
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from nextix.github.app_auth import GitHubAppAuth
from nextix.github.schemas import (
    GhCheckRun,
    GhComment,
    GhCommitStatus,
    GhInstallation,
    GhIssue,
    GhPullRequest,
    GhRepository,
    GhReview,
    GhReviewComment,
)


class DiffTooLarge(Exception):
    """GitHub won't render this PR's diff (too many files or lines)."""


_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubClient:
    def __init__(self, auth: GitHubAppAuth, http: httpx.AsyncClient, api_url: str) -> None:
        self._auth = auth
        self._http = http
        self._api_url = api_url.rstrip("/")

    @property
    def auth(self) -> GitHubAppAuth:
        return self._auth

    async def _headers(self, installation_id: int) -> dict[str, str]:
        token = await self._auth.installation_token(installation_id)
        return {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self._auth.api_version,
        }

    async def _get(
        self, installation_id: int, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        url = path if path.startswith("http") else f"{self._api_url}{path}"
        resp = await self._http.get(
            url, headers=await self._headers(installation_id), params=params
        )
        resp.raise_for_status()
        return resp.json()

    async def _post(self, installation_id: int, path: str, body: dict[str, Any]) -> Any:
        resp = await self._http.post(
            f"{self._api_url}{path}", headers=await self._headers(installation_id), json=body
        )
        resp.raise_for_status()
        return resp.json()

    async def _patch(self, installation_id: int, path: str, body: dict[str, Any]) -> Any:
        resp = await self._http.patch(
            f"{self._api_url}{path}", headers=await self._headers(installation_id), json=body
        )
        resp.raise_for_status()
        return resp.json()

    async def _delete(self, installation_id: int, path: str) -> None:
        resp = await self._http.delete(
            f"{self._api_url}{path}", headers=await self._headers(installation_id)
        )
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()

    async def _paginate(
        self,
        installation_id: int,
        path: str,
        params: dict[str, Any],
        *,
        items_key: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Follow Link rel="next". ``items_key`` is for endpoints that wrap the list."""
        url: str | None = f"{self._api_url}{path}"
        query: dict[str, Any] | None = {**params, "per_page": 100}
        while url:
            resp = await self._http.get(
                url, headers=await self._headers(installation_id), params=query
            )
            resp.raise_for_status()
            body = resp.json()
            for item in body[items_key] if items_key else body:
                yield item
            match = _NEXT_LINK.search(resp.headers.get("link", ""))
            url = match.group(1) if match else None
            query = None  # the next link already carries the query string

    async def get_repo_installation(self, owner: str, name: str) -> GhInstallation:
        resp = await self._http.get(
            f"{self._api_url}/repos/{owner}/{name}/installation",
            headers={
                "Authorization": f"Bearer {self._auth.app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": self._auth.api_version,
            },
        )
        resp.raise_for_status()
        return GhInstallation.model_validate(resp.json())

    async def get_repo(self, installation_id: int, owner: str, name: str) -> GhRepository:
        return GhRepository.model_validate(
            await self._get(installation_id, f"/repos/{owner}/{name}")
        )

    async def list_installation_repos(self, installation_id: int) -> list[GhRepository]:
        """Every repo the installation can currently access."""
        return [
            GhRepository.model_validate(item)
            async for item in self._paginate(
                installation_id, "/installation/repositories", {}, items_key="repositories"
            )
        ]

    async def get_issue(
        self, installation_id: int, owner: str, name: str, number: int
    ) -> GhIssue | None:
        try:
            data = await self._get(installation_id, f"/repos/{owner}/{name}/issues/{number}")
        except httpx.HTTPStatusError as exc:
            # 404: deleted or transferred. 410: issues disabled / deleted.
            if exc.response.status_code in (404, 410):
                return None
            raise
        return GhIssue.model_validate(data)

    async def list_issues(
        self, installation_id: int, owner: str, name: str, *, labels: str, state: str = "all"
    ) -> AsyncIterator[GhIssue]:
        """Issues only. The REST endpoint also returns PRs, which are filtered out."""
        async for item in self._paginate(
            installation_id,
            f"/repos/{owner}/{name}/issues",
            {"labels": labels, "state": state},
        ):
            issue = GhIssue.model_validate(item)
            if issue.pull_request is None:
                yield issue

    async def list_pulls(
        self, installation_id: int, owner: str, name: str, *, state: str = "all"
    ) -> AsyncIterator[GhPullRequest]:
        async for item in self._paginate(
            installation_id, f"/repos/{owner}/{name}/pulls", {"state": state}
        ):
            yield GhPullRequest.model_validate(item)

    # ------------------------------------------------------------------ writes / triage

    async def list_labels(self, installation_id: int, owner: str, name: str) -> list[str]:
        return [
            item["name"]
            async for item in self._paginate(installation_id, f"/repos/{owner}/{name}/labels", {})
        ]

    async def create_label(
        self,
        installation_id: int,
        owner: str,
        name: str,
        *,
        label: str,
        color: str,
        description: str = "",
    ) -> None:
        try:
            await self._post(
                installation_id,
                f"/repos/{owner}/{name}/labels",
                {"name": label, "color": color, "description": description},
            )
        except httpx.HTTPStatusError as exc:
            # 422 "already_exists": someone created it concurrently. That's fine.
            if exc.response.status_code != 422:
                raise

    async def create_issue(
        self,
        installation_id: int,
        owner: str,
        name: str,
        *,
        title: str,
        body: str,
        labels: list[str],
    ) -> GhIssue:
        data = await self._post(
            installation_id,
            f"/repos/{owner}/{name}/issues",
            {"title": title, "body": body, "labels": labels},
        )
        return GhIssue.model_validate(data)

    async def create_comment(
        self, installation_id: int, owner: str, name: str, number: int, body: str
    ) -> None:
        await self._post(
            installation_id, f"/repos/{owner}/{name}/issues/{number}/comments", {"body": body}
        )

    async def list_issue_comments(
        self, installation_id: int, owner: str, name: str, number: int, *, since: datetime | None
    ) -> list[GhComment]:
        """Comments on an issue, oldest first; with ``since``, only those updated after it."""
        params: dict[str, Any] = {"since": since.isoformat()} if since else {}
        return [
            GhComment.model_validate(item)
            async for item in self._paginate(
                installation_id, f"/repos/{owner}/{name}/issues/{number}/comments", params
            )
        ]

    async def list_file_paths(
        self, installation_id: int, owner: str, name: str, ref: str
    ) -> list[str]:
        """File paths on ``ref``. Empty for an empty repo. May be truncated by GitHub."""
        try:
            data = await self._get(
                installation_id,
                f"/repos/{owner}/{name}/git/trees/{ref}",
                params={"recursive": "1"},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 409):  # no such ref / empty repository
                return []
            raise
        return sorted(e["path"] for e in data.get("tree", []) if e.get("type") == "blob")

    # ------------------------------------------------------------------ files

    async def get_file(
        self, installation_id: int, owner: str, name: str, path: str, *, ref: str
    ) -> tuple[str, str] | None:
        """(text, blob sha) of a file at ``ref``, or None if it doesn't exist there."""
        try:
            data = await self._get(
                installation_id,
                f"/repos/{owner}/{name}/contents/{quote(path)}",
                params={"ref": ref},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        if not isinstance(data, dict) or data.get("type") != "file":
            return None
        try:
            raw = base64.b64decode(data.get("content") or "")
        except (binascii.Error, ValueError):
            return None
        return raw.decode("utf-8", errors="replace"), str(data.get("sha") or "")

    # ------------------------------------------------------------------ review

    async def get_pull_diff(
        self, installation_id: int, owner: str, name: str, number: int, *, max_bytes: int
    ) -> tuple[str, bool]:
        """The PR's unified diff and whether it was cut at ``max_bytes``.

        Raises DiffTooLarge when GitHub refuses to render it (very large PRs).
        """
        headers = {
            **(await self._headers(installation_id)),
            "Accept": "application/vnd.github.diff",
        }
        url = f"{self._api_url}/repos/{owner}/{name}/pulls/{number}"
        async with self._http.stream("GET", url, headers=headers) as resp:
            if resp.status_code in (406, 422):
                raise DiffTooLarge(number)
            resp.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            truncated = False
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    truncated = True
                    break
        body = b"".join(chunks)[:max_bytes]
        return body.decode("utf-8", errors="replace"), truncated

    async def list_check_runs(
        self, installation_id: int, owner: str, name: str, sha: str
    ) -> list[GhCheckRun]:
        """Every check run on a commit (paginated)."""
        return [
            GhCheckRun.model_validate(item)
            async for item in self._paginate(
                installation_id,
                f"/repos/{owner}/{name}/commits/{sha}/check-runs",
                {},
                items_key="check_runs",
            )
        ]

    async def list_commit_statuses(
        self, installation_id: int, owner: str, name: str, sha: str
    ) -> list[GhCommitStatus]:
        """The latest status per context on a commit (the combined status)."""
        data = await self._get(
            installation_id, f"/repos/{owner}/{name}/commits/{sha}/status", {"per_page": 100}
        )
        return [GhCommitStatus.model_validate(s) for s in (data or {}).get("statuses") or []]

    async def get_review(
        self, installation_id: int, owner: str, name: str, number: int, review_id: int
    ) -> GhReview:
        data = await self._get(
            installation_id, f"/repos/{owner}/{name}/pulls/{number}/reviews/{review_id}"
        )
        return GhReview.model_validate(data)

    async def list_review_comments(
        self, installation_id: int, owner: str, name: str, number: int, review_id: int
    ) -> list[GhReviewComment]:
        """The inline comments of one review, oldest first."""
        return [
            GhReviewComment.model_validate(item)
            async for item in self._paginate(
                installation_id,
                f"/repos/{owner}/{name}/pulls/{number}/reviews/{review_id}/comments",
                {},
            )
        ]

    # ------------------------------------------------------------------ runs

    async def branch_exists(self, installation_id: int, owner: str, name: str, branch: str) -> bool:
        """False for a missing branch, including every branch of an empty repository."""
        try:
            await self._get(
                installation_id, f"/repos/{owner}/{name}/branches/{quote(branch, safe='')}"
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return False
            raise
        return True

    async def find_open_pull(
        self, installation_id: int, owner: str, name: str, *, branch: str
    ) -> GhPullRequest | None:
        """The open PR whose head is ``owner:branch`` in this repo, if any."""
        data = await self._get(
            installation_id,
            f"/repos/{owner}/{name}/pulls",
            params={"head": f"{owner}:{branch}", "state": "open", "per_page": 10},
        )
        return GhPullRequest.model_validate(data[0]) if data else None

    async def create_pull(
        self,
        installation_id: int,
        owner: str,
        name: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> GhPullRequest:
        data = await self._post(
            installation_id,
            f"/repos/{owner}/{name}/pulls",
            {"title": title, "body": body, "head": head, "base": base},
        )
        return GhPullRequest.model_validate(data)

    async def update_pull(
        self, installation_id: int, owner: str, name: str, number: int, *, title: str, body: str
    ) -> GhPullRequest:
        data = await self._patch(
            installation_id,
            f"/repos/{owner}/{name}/pulls/{number}",
            {"title": title, "body": body},
        )
        return GhPullRequest.model_validate(data)

    async def add_labels(
        self, installation_id: int, owner: str, name: str, number: int, labels: list[str]
    ) -> None:
        await self._post(
            installation_id, f"/repos/{owner}/{name}/issues/{number}/labels", {"labels": labels}
        )

    async def remove_label(
        self, installation_id: int, owner: str, name: str, number: int, label: str
    ) -> None:
        await self._delete(
            installation_id,
            f"/repos/{owner}/{name}/issues/{number}/labels/{quote(label, safe='')}",
        )
