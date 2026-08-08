from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings


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

    with (
        patch("app.main.supabase", database),
        patch.object(settings, "SUPABASE_SERVICE_ROLE_KEY", "test-service-role"),
    ):
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

    with (
        patch("app.main.supabase", database),
        patch.object(settings, "SUPABASE_SERVICE_ROLE_KEY", "test-service-role"),
    ):
        response = TestClient(app).get("/readiness")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    queried_tables = [call.args[0] for call in database.table.call_args_list]
    assert queried_tables == [
        "workspaces",
        "self_service_magic_links",
        "portal_sessions",
        "public_rate_limits",
        "public_leads",
        "asset_retention_records",
    ]


def test_readiness_fails_closed_without_explicit_service_role_key() -> None:
    database = MagicMock()

    with (
        patch("app.main.supabase", database),
        patch.object(settings, "SELF_SERVICE_SIGNUP_ENABLED", True),
        patch.object(settings, "SUPABASE_SERVICE_ROLE_KEY", None),
    ):
        response = TestClient(app).get("/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "service": "sunbeat-api",
        "configuration": "unavailable",
    }
    database.table.assert_not_called()
