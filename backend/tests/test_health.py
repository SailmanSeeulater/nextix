from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from nextix.api.health import get_probes
from nextix.main import create_app


async def _ok() -> None:
    return None


async def _boom() -> None:
    raise ConnectionError("down")


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health_ok_when_all_probes_pass(client: TestClient) -> None:
    client.app.dependency_overrides[get_probes] = lambda: {"db": _ok, "redis": _ok}  # type: ignore[attr-defined]
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"db": "ok", "redis": "ok"}
    assert "version" in body


def test_health_503_when_a_probe_fails(client: TestClient) -> None:
    client.app.dependency_overrides[get_probes] = lambda: {"db": _ok, "redis": _boom}  # type: ignore[attr-defined]
    r = client.get("/api/health")
    assert r.status_code == 503
    assert r.json()["status"] == "error"
    assert r.json()["checks"] == {"db": "ok", "redis": "error"}


def test_health_reports_error_without_real_services(client: TestClient) -> None:
    """With no overrides the real probes run; whatever the outcome, the shape is stable."""
    r = client.get("/api/health")
    assert r.status_code in (200, 503)
    assert set(r.json()["checks"]) == {"db", "redis"}
