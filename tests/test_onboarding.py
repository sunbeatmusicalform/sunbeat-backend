from __future__ import annotations

import os
import sys
import types
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")
os.environ.setdefault("INTERNAL_ADMIN_TOKEN", "test-admin-token")

try:
    import supabase  # noqa: F401
except ModuleNotFoundError:
    supabase_stub = types.ModuleType("supabase")
    supabase_stub.create_client = lambda *_args, **_kwargs: object()
    sys.modules["supabase"] = supabase_stub

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.modules import onboarding
from app.modules.portal_session import issue_portal_token


class _FakeTable:
    def __init__(self, owner: "_FakeSupabase", name: str) -> None:
        self.owner = owner
        self.name = name
        self.filters: list[tuple[str, object]] = []
        self.operation = "select"
        self.payload: dict | None = None

    def select(self, _fields: str) -> "_FakeTable":
        return self

    def eq(self, key: str, value: object) -> "_FakeTable":
        self.filters.append((key, value))
        return self

    def limit(self, _count: int) -> "_FakeTable":
        return self

    def insert(self, payload: dict) -> "_FakeTable":
        self.operation = "insert"
        self.payload = deepcopy(payload)
        return self

    def update(self, payload: dict) -> "_FakeTable":
        self.operation = "update"
        self.payload = deepcopy(payload)
        return self

    def upsert(self, payload: dict, on_conflict: str) -> "_FakeTable":
        assert on_conflict == "workspace_slug,workflow_type"
        self.operation = "upsert"
        self.payload = deepcopy(payload)
        return self

    def _matched(self) -> list[dict]:
        return [
            row
            for row in self.owner.tables.setdefault(self.name, [])
            if all(row.get(key) == value for key, value in self.filters)
        ]

    def execute(self) -> SimpleNamespace:
        if self.name == "setup_ai_action_audit" and not self.owner.audit_available:
            raise RuntimeError("setup_ai_action_audit is missing")

        rows = self.owner.tables.setdefault(self.name, [])
        if self.operation == "insert":
            record = {"id": f"audit-{len(rows) + 1}", **(self.payload or {})}
            rows.append(record)
            return SimpleNamespace(data=[deepcopy(record)])
        if self.operation == "update":
            matched = self._matched()
            for row in matched:
                row.update(deepcopy(self.payload or {}))
            return SimpleNamespace(data=deepcopy(matched))
        if self.operation == "upsert":
            payload = self.payload or {}
            matched = [
                row
                for row in rows
                if row.get("workspace_slug") == payload.get("workspace_slug")
                and row.get("workflow_type") == payload.get("workflow_type")
            ]
            if matched:
                matched[0].update(deepcopy(payload))
                record = matched[0]
            else:
                record = deepcopy(payload)
                rows.append(record)
            return SimpleNamespace(data=[deepcopy(record)])
        return SimpleNamespace(data=deepcopy(self._matched()))


class _FakeSupabase:
    def __init__(
        self,
        *,
        audit_available: bool = True,
        self_service: bool = True,
        override: dict | None = None,
    ) -> None:
        self.audit_available = audit_available
        self.auth = SimpleNamespace(
            admin=SimpleNamespace(
                get_user_by_id=lambda _user_id: SimpleNamespace(
                    user=SimpleNamespace(user_metadata={"self_service": self_service})
                )
            )
        )
        self.tables: dict[str, list[dict]] = {
            "workspaces": [
                {
                    "slug": "demo",
                    "name": "Demo Records",
                    "plan_id": "free",
                    "plans": {"submissions_month": 50},
                }
            ],
            "workspace_users": [
                {"workspace_slug": "demo", "user_id": "demo-owner", "role": "owner"}
            ],
            "workspace_plan_overrides": [override] if override else [],
            "workspace_branding": [
                {
                    "workspace_slug": "demo",
                    "workspace_name": "Demo Records",
                    "enabled_workflows": ["release_intake"],
                }
            ],
            "workspace_workflow_settings": [],
            "setup_ai_action_audit": [],
        }

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self, name)


def _client(fake: _FakeSupabase) -> tuple[TestClient, dict[str, str]]:
    settings.INTERNAL_ADMIN_TOKEN = "test-admin-token"
    token = issue_portal_token("demo")
    patcher = patch.object(onboarding, "supabase", fake)
    patcher.start()
    client = TestClient(app)
    client._onboarding_patcher = patcher  # type: ignore[attr-defined]
    return client, {"X-Portal-Token": token}


def _profile(**overrides: object) -> dict:
    value = {
        "operationType": "label",
        "teamSize": "2-5",
        "monthlyVolume": "11-50",
        "workflowTypes": ["release_intake"],
        "integrations": ["airtable", "google_drive"],
        "primaryGoal": "Receber lançamentos completos.",
    }
    value.update(overrides)
    return value


def test_onboarding_requires_workspace_portal_session() -> None:
    response = TestClient(app).get("/workspaces/demo/onboarding")
    assert response.status_code == 401


def test_free_plan_preview_filters_workflows_and_explains_retention() -> None:
    fake = _FakeSupabase()
    client, headers = _client(fake)
    response = client.post(
        "/workspaces/demo/onboarding",
        headers=headers,
        json={
            "operation": "preview_patch",
            "profile": _profile(workflowTypes=["release_intake", "rights_clearance"]),
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabledWorkflows"] == ["release_intake"]
    assert "60 dias" in data["warnings"][0]
    assert data["warningCodes"] == ["free_asset_retention_60_days"]
    assert data["previewToken"]
    assert fake.tables["setup_ai_action_audit"][0]["status"] == "succeeded"


def test_apply_requires_matching_preview_and_persists_profile() -> None:
    fake = _FakeSupabase()
    client, headers = _client(fake)
    profile = _profile()
    preview_response = client.post(
        "/workspaces/demo/onboarding",
        headers=headers,
        json={"operation": "preview_patch", "profile": profile},
    )
    preview = preview_response.json()["data"]
    apply_response = client.post(
        "/workspaces/demo/onboarding",
        headers=headers,
        json={
            "operation": "apply_patch",
            "profile": profile,
            "preview_token": preview["previewToken"],
        },
    )
    assert apply_response.status_code == 200
    settings_rows = fake.tables["workspace_workflow_settings"]
    assert len(settings_rows) == 1
    onboarding_value = settings_rows[0]["extra_settings"]["onboarding"]
    assert onboarding_value["profile"]["primaryGoal"] == "Receber lançamentos completos."
    assert onboarding_value["enabled_workflows"] == ["release_intake"]
    assert onboarding_value["provisioning"]["mode"] == "self_service"
    assert onboarding_value["provisioning"]["integrations"] == {
        "airtable": "pending_authorization",
        "google_drive": "pending_authorization",
    }
    assert fake.tables["workspace_branding"][0]["enabled_workflows"] == ["release_intake"]
    assert fake.tables["setup_ai_action_audit"][-1]["status"] == "succeeded"


def test_custom_override_is_the_same_access_map_used_by_motoschema() -> None:
    fake = _FakeSupabase(
        override={
            "workspace_slug": "demo",
            "max_submissions_month": None,
            "enabled_workflow_types": [
                "release_intake",
                "rights_clearance",
                "company_registry",
            ],
        }
    )
    client, headers = _client(fake)

    response = client.get("/workspaces/demo/onboarding", headers=headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["planId"] == "free"
    assert data["accessMode"] == "custom"
    assert data["allowedWorkflowTypes"] == [
        "release_intake",
        "rights_clearance",
        "company_registry",
    ]


def test_managed_workspace_updates_profile_without_touching_active_workflows() -> None:
    fake = _FakeSupabase(self_service=False)
    fake.tables["workspace_workflow_settings"].append(
        {
            "workspace_slug": "demo",
            "workflow_type": "rights_clearance",
            "extra_settings": {"airtable": {"enabled": True}},
        }
    )
    original = deepcopy(fake.tables["workspace_branding"])
    client, headers = _client(fake)
    profile = _profile()
    initial_response = client.get("/workspaces/demo/onboarding", headers=headers)
    assert "rights_clearance" in initial_response.json()["data"]["allowedWorkflowTypes"]
    preview_response = client.post(
        "/workspaces/demo/onboarding",
        headers=headers,
        json={"operation": "preview_patch", "profile": profile},
    )
    preview = preview_response.json()["data"]

    assert preview["provisioningMode"] == "profile_only"
    assert any("não alterará" in warning for warning in preview["warnings"])

    apply_response = client.post(
        "/workspaces/demo/onboarding",
        headers=headers,
        json={
            "operation": "apply_patch",
            "profile": profile,
            "preview_token": preview["previewToken"],
        },
    )

    assert apply_response.status_code == 200
    assert fake.tables["workspace_branding"] == original
    onboarding_row = next(
        row
        for row in fake.tables["workspace_workflow_settings"]
        if row["workflow_type"] == onboarding.ONBOARDING_WORKFLOW_TYPE
    )
    onboarding_value = onboarding_row["extra_settings"]["onboarding"]
    assert onboarding_value["provisioning"]["mode"] == "profile_only"
    assert onboarding_value["provisioning"]["workflow_status"] == "unchanged"


def test_apply_is_blocked_when_audit_is_unavailable() -> None:
    fake = _FakeSupabase(audit_available=False)
    client, headers = _client(fake)
    preview_response = client.post(
        "/workspaces/demo/onboarding",
        headers=headers,
        json={"operation": "preview_patch", "profile": _profile()},
    )
    preview = preview_response.json()["data"]
    apply_response = client.post(
        "/workspaces/demo/onboarding",
        headers=headers,
        json={
            "operation": "apply_patch",
            "profile": _profile(),
            "preview_token": preview["previewToken"],
        },
    )
    assert apply_response.status_code == 503
    assert fake.tables["workspace_workflow_settings"] == []


def test_apply_rejects_changed_profile_after_preview() -> None:
    fake = _FakeSupabase()
    client, headers = _client(fake)
    preview_response = client.post(
        "/workspaces/demo/onboarding",
        headers=headers,
        json={"operation": "preview_patch", "profile": _profile()},
    )
    preview = preview_response.json()["data"]
    response = client.post(
        "/workspaces/demo/onboarding",
        headers=headers,
        json={
            "operation": "apply_patch",
            "profile": _profile(primaryGoal="Objetivo alterado depois da prévia"),
            "preview_token": preview["previewToken"],
        },
    )
    assert response.status_code == 409
    assert fake.tables["workspace_workflow_settings"] == []
