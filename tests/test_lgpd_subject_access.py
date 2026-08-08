from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.lgpd_subject_access import SubjectAccessError, build_qa_subject_access_report


class _Query:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = deepcopy(rows)
        self.filters: list[tuple[str, Any]] = []
        self.limit_value: int | None = None

    def select(self, _columns: str) -> "_Query":
        return self

    def eq(self, field: str, value: Any) -> "_Query":
        self.filters.append((field, value))
        return self

    def limit(self, value: int) -> "_Query":
        self.limit_value = value
        return self

    def execute(self) -> SimpleNamespace:
        rows = [row for row in self.rows if all(row.get(field) == value for field, value in self.filters)]
        return SimpleNamespace(data=rows[: self.limit_value] if self.limit_value is not None else rows)


class _Database:
    def __init__(self, *, self_service: bool = True, auth_email: str = "qa@example.com") -> None:
        slug = "sunbeat-qa-access-20260808"
        self.tables: dict[str, list[dict[str, Any]]] = {
            "workspaces": [{"slug": slug, "name": "QA Access", "owner_email": "qa@example.com", "plan_id": "free"}],
            "workspace_users": [{"workspace_slug": slug, "user_id": "user-qa", "role": "owner"}],
            "workspace_branding": [{"workspace_slug": slug, "workspace_name": "QA Access"}],
            "workspace_workflow_settings": [],
            "workspace_plan_overrides": [],
            "self_service_magic_links": [{"workspace_slug": slug, "user_id": "user-qa", "token_hash": "do-not-export"}],
            "portal_sessions": [{"workspace_slug": slug, "session_id": "session-1"}],
            "asset_retention_records": [],
            "setup_ai_action_audit": [],
            "people_registry_records": [],
            "release_intake_drafts": [{"client_slug": slug, "draft_token": "secret-draft", "values": {"email": "qa@example.com"}}],
            "submissions": [{"client_slug": slug, "id": "submission-1", "payload": {"edit_token": "secret-edit"}}],
        }
        user = SimpleNamespace(
            id="user-qa",
            email=auth_email,
            app_metadata={"self_service": self_service, "asset_retention_days": 60},
            user_metadata={"full_name": "QA Person", "terms_version": "sunbeat-terms-2026-08-07"},
        )
        self.auth = SimpleNamespace(admin=SimpleNamespace(get_user_by_id=lambda _user_id: SimpleNamespace(user=user)))

    def table(self, name: str) -> _Query:
        return _Query(self.tables[name])


def _report(database: _Database, **overrides: Any) -> dict[str, Any]:
    payload = {
        "workspace_slug": "sunbeat-qa-access-20260808",
        "email": "qa@example.com",
        "request_id": "LGPD-QA-20260808-01",
        "generated_at": datetime(2026, 8, 8, 18, 0, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return build_qa_subject_access_report(database, **payload)


def test_access_report_is_read_only_and_redacts_security_values() -> None:
    report = _report(_Database())

    assert report["mode"] == "read_only"
    assert report["record_counts"]["submissions"] == 1
    assert report["records"]["self_service_magic_links"]["rows"][0]["token_hash"] == "[redacted]"
    assert report["records"]["release_intake_drafts"]["rows"][0]["draft_token"] == "[redacted]"
    assert report["records"]["submissions"]["rows"][0]["payload"]["edit_token"] == "[redacted]"
    assert report["safety"]["database_write"] is False


@pytest.mark.parametrize("workspace_slug", ["atabaque", "customer-production", "qa"])
def test_access_report_rejects_non_isolated_workspace_names(workspace_slug: str) -> None:
    with pytest.raises(SubjectAccessError, match="isolated QA"):
        _report(_Database(), workspace_slug=workspace_slug)


def test_access_report_rejects_managed_workspace() -> None:
    with pytest.raises(SubjectAccessError, match="Managed"):
        _report(_Database(self_service=False))


def test_access_report_requires_matching_owner_email() -> None:
    with pytest.raises(SubjectAccessError, match="owner"):
        _report(_Database(), email="someone-else@example.com")


def test_access_report_requires_matching_auth_identity() -> None:
    with pytest.raises(SubjectAccessError, match="Auth identity"):
        _report(_Database(auth_email="other@example.com"))
