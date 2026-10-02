"""Thin client for the nexTix HTTP API."""

import uuid
from typing import Any

import httpx

# Triage calls Claude and can take a while; everything else is quick.
CREATE_TIMEOUT_S = 180.0
DEFAULT_TIMEOUT_S = 20.0


class ApiError(Exception):
    """A request failed. ``message`` is safe to show the user."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NextixApi:
    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/")
        # Only a client we made is ours to close; an injected one belongs to the caller.
        self._owns_http = http is None
        self._http = http or httpx.Client()
        self._headers = {"Authorization": f"Bearer {token}"}

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "NextixApi":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(self, method: str, path: str, *, timeout: float, **kwargs: Any) -> Any:
        # Per-call headers (e.g. Idempotency-Key) go on top of the auth header.
        headers = {**self._headers, **kwargs.pop("headers", {})}
        try:
            resp = self._http.request(
                method, f"{self._base}{path}", headers=headers, timeout=timeout, **kwargs
            )
        except httpx.TimeoutException as exc:
            raise ApiError(f"The nexTix API at {self._base} took too long to respond.") from exc
        except httpx.TransportError as exc:
            raise ApiError(
                f"Can't reach the nexTix API at {self._base}. Is it running? "
                "(Check `docker compose ps`, or run `nextix login` to change the URL.)"
            ) from exc
        if resp.status_code == 401:
            raise ApiError("The API rejected your token. Run `nextix login` again.", 401)
        if resp.is_error:
            raise ApiError(_detail(resp), resp.status_code)
        return resp.json()

    def check(self) -> None:
        # Any authenticated endpoint proves the token; the repo list is small, the board isn't.
        self._request("GET", "/api/repos", timeout=DEFAULT_TIMEOUT_S)

    def create_ticket(
        self,
        *,
        repo: str,
        prompt: str,
        labels: list[str],
        triage: bool,
        created_via: str = "cli",
    ) -> dict[str, Any]:
        body = {
            "repo": repo,
            "prompt": prompt,
            "labels": labels,
            "triage": triage,
            "created_via": created_via,
        }
        # One key per attempt: if this call times out while the API is still working and
        # the user runs the command again, the API returns the first ticket rather than
        # filing a second GitHub issue.
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        result: dict[str, Any] = self._request(
            "POST", "/api/tickets", json=body, headers=headers, timeout=CREATE_TIMEOUT_S
        )
        return result

    def list_tickets(
        self, *, repo: str | None = None, column: str | None = None
    ) -> list[dict[str, Any]]:
        params = {k: v for k, v in {"repo": repo, "column": column}.items() if v}
        result: list[dict[str, Any]] = self._request(
            "GET", "/api/tickets", params=params, timeout=DEFAULT_TIMEOUT_S
        )
        return result

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._request(
            "GET", f"/api/tickets/{ticket_id}", timeout=DEFAULT_TIMEOUT_S
        )
        return result


def _detail(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list) and detail:  # FastAPI validation errors
        first = detail[0]
        where = ".".join(str(p) for p in first.get("loc", [])[1:])
        return f"Invalid {where or 'request'}: {first.get('msg', 'bad value')}"
    return f"The API returned HTTP {resp.status_code}."
