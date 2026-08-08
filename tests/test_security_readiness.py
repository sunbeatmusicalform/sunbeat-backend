from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app


def test_security_headers_and_request_id_are_present() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert response.headers["x-request-id"]


def test_readiness_reports_database_failure_without_details() -> None:
    database = MagicMock()
    database.table.side_effect = RuntimeError("secret connection details")

    with patch("app.main.supabase", database):
        response = TestClient(app).get("/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "service": "sunbeat-api",
        "database": "unavailable",
    }
    assert "secret" not in response.text


def test_readiness_passes_when_database_is_reachable() -> None:
    database = MagicMock()
    database.table.return_value.select.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=[])

    with patch("app.main.supabase", database):
        response = TestClient(app).get("/readiness")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
