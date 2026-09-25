"""Thin typed wrapper over the GitHub REST API calls nexTix needs.

All calls authenticate as an installation except ``get_repo_installation``,
which uses the app JWT to discover which installation covers a repo.
"""

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from nextix.github.app_auth import GitHubAppAuth
from nextix.github.schemas import GhInstallation, GhIssue, GhPullRequest, GhRepository

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
