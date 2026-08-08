from __future__ import annotations

import os
import sys
import time
import types
from datetime import datetime, timezone
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
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
from app.modules import onboarding, portal_session, self_service_auth


class _Query:
    def __init__(self, owner: "_FakeSupabase", name: str) -> None:
        self.owner = owner
        self.name = name
        self.filters: list[tuple[str, object]] = []
        self.operation = "select"
        self.payload: dict | None = None

    def select(self, _fields: str) -> "_Query": return self
    def limit(self, _count: int) -> "_Query": return self

    def eq(self, key: str, value: object) -> "_Query":
        self.filters.append((key, value))
        return self

    def insert(self, payload: dict) -> "_Query":
        self.operation = "insert"
        self.payload = deepcopy(payload)
        return self

    def update(self, payload: dict) -> "_Query":
        self.operation = "update"
        self.payload = deepcopy(payload)
        return self

    def delete(self) -> "_Query":
        self.operation = "delete"
        return self

    def upsert(self, payload: dict, on_conflict: str) -> "_Query":
        assert on_conflict == "workspace_slug,workflow_type"
        self.operation = "upsert"
        self.payload = deepcopy(payload)
        return self

    def _matched(self) -> list[dict]:
        return [row for row in self.owner.tables.setdefault(self.name, []) if all(row.get(k) == v for k, v in self.filters)]

    def execute(self) -> SimpleNamespace:
        rows = self.owner.tables.setdefault(self.name, [])
        if self.operation == "insert":
            record = deepcopy(self.payload or {})
            if self.name == "setup_ai_action_audit":
                record = {"id": f"audit-{len(rows) + 1}", **record}
            rows.append(record)
            return SimpleNamespace(data=[deepcopy(record)])
        if self.operation == "delete":
            matched = self._matched()
            self.owner.tables[self.name] = [row for row in rows if row not in matched]
            return SimpleNamespace(data=deepcopy(matched))
        if self.operation == "update":
            matched = self._matched()
            for row in matched:
                row.update(deepcopy(self.payload or {}))
            return SimpleNamespace(data=deepcopy(matched))
        if self.operation == "upsert":
            matched = [
                row for row in rows
                if row.get("workspace_slug") == self.payload.get("workspace_slug")
                and row.get("workflow_type") == self.payload.get("workflow_type")
            ]
            if matched:
                matched[0].update(deepcopy(self.payload))
                record = matched[0]
            else:
                record = deepcopy(self.payload)
                rows.append(record)
            return SimpleNamespace(data=[deepcopy(record)])
        return SimpleNamespace(data=deepcopy(self._matched()))


class _Rpc:
    def __init__(self, data: object) -> None:
        self.data = data

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self.data)


class _Admin:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []
        self.deleted: list[str] = []

    def create_user(self, attributes: dict) -> SimpleNamespace:
        self.created.append(deepcopy(attributes))
        return SimpleNamespace(user=SimpleNamespace(id="11111111-1111-1111-1111-111111111111"))

    def update_user_by_id(self, user_id: str, attributes: dict) -> SimpleNamespace:
        self.updated.append((user_id, deepcopy(attributes)))
        return SimpleNamespace(user=SimpleNamespace(id=user_id))

    def delete_user(self, user_id: str) -> None:
        self.deleted.append(user_id)

    def get_user_by_id(self, user_id: str) -> SimpleNamespace:
        created = self.created[0] if self.created else {}
        return SimpleNamespace(
            user=SimpleNamespace(
                id=user_id,
                email=created.get("email") or "felipe@example.com",
                user_metadata=deepcopy(created.get("user_metadata") or {"self_service": True}),
            )
        )


class _FakeSupabase:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {
            "workspaces": [],
            "workspace_users": [],
            "workspace_branding": [],
            "self_service_magic_links": [],
            "portal_sessions": [],
            "workspace_plan_overrides": [],
            "workspace_workflow_settings": [],
            "setup_ai_action_audit": [],
        }
        self.admin = _Admin()
        self.auth = SimpleNamespace(admin=self.admin)

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def rpc(self, name: str, payload: dict) -> _Rpc:
        if name == "consume_public_rate_limit":
            return _Rpc(True)
        if name == "consume_self_service_magic_link":
            now = datetime.now(timezone.utc)
            for row in self.tables["self_service_magic_links"]:
                expires_at = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                if (
                    row["token_id"] == payload["p_token_id"]
                    and row["token_hash"] == payload["p_token_hash"]
                    and row["user_id"] == payload["p_user_id"]
                    and row["workspace_slug"] == payload["p_workspace_slug"]
                    and not row.get("consumed_at")
                    and expires_at > now
                ):
                    row["consumed_at"] = now.isoformat()
                    return _Rpc(True)
            return _Rpc(False)
        raise AssertionError(f"unexpected rpc: {name}")


def _client(fake: _FakeSupabase) -> TestClient:
    settings.INTERNAL_ADMIN_TOKEN = "test-admin-token"
    settings.SELF_SERVICE_SIGNUP_ENABLED = True
    settings.FRONTEND_BASE_URL = "https://sunbeat.pro"
    email_mock = patch.object(
        self_service_auth,
        "send_workspace_magic_link_email",
        return_value={"provider": "test"},
    )
    patches = (
        patch.object(self_service_auth, "supabase", fake),
        email_mock,
    )
    for item in patches: item.start()
    client = TestClient(app)
    client._self_service_patches = patches  # type: ignore[attr-defined]
    client._magic_email_mock = email_mock.target.send_workspace_magic_link_email  # type: ignore[attr-defined]
    return client


def _last_magic_token(client: TestClient) -> str:
    link = client._magic_email_mock.call_args.kwargs["magic_link"]  # type: ignore[attr-defined]
    return parse_qs(urlparse(link).query)["token"][0]


def _signup_payload(**values: object) -> dict:
    payload = {
        "name": "Felipe Fonseca",
        "email": "felipe@example.com",
        "workspace_name": "Sol Records",
        "plan_intent": "starter",
        "terms_accepted": True,
        "company_website": "",
        "form_started_at": int(time.time() * 1000) - 2000,
    }
    payload.update(values)
    return payload


def test_signup_provisions_free_workspace_and_sends_link() -> None:
    fake = _FakeSupabase()
    client = _client(fake)
    response = client.post("/auth/signup", json=_signup_payload(), headers={"Host": "sunbeat.com.br"})
    assert response.status_code == 200
    assert response.json()["workspace_slug"] == "sol-records"
    assert fake.tables["workspaces"][0]["plan_id"] == "free"
    assert fake.tables["workspace_users"][0]["role"] == "owner"
    assert fake.tables["workspace_branding"][0]["enabled_workflows"] == ["release_intake"]
    assert fake.admin.created[0]["user_metadata"]["self_service"] is True
    assert fake.admin.created[0]["user_metadata"]["asset_retention_days"] == 60


def test_signup_honeypot_returns_neutral_success_without_writes() -> None:
    fake = _FakeSupabase()
    response = _client(fake).post("/auth/signup", json=_signup_payload(company_website="spam.example"))
    assert response.status_code == 200
    assert fake.tables["workspaces"] == []
    assert fake.admin.created == []


def test_signup_rejects_reserved_workspace() -> None:
    response = _client(_FakeSupabase()).post("/auth/signup", json=_signup_payload(workspace_name="Admin"))
    assert response.status_code == 409


def test_callback_confirms_user_and_redirects_to_portal_fragment() -> None:
    fake = _FakeSupabase()
    client = _client(fake)
    signup = client.post("/auth/signup", json=_signup_payload())
    assert signup.status_code == 200
    token = _last_magic_token(client)
    response = client.get(f"/auth/callback?token={token}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("https://sunbeat.pro/portal/sol-records#portal_token=")
    assert fake.admin.updated == [
        ("11111111-1111-1111-1111-111111111111", {"email_confirm": True})
    ]
    assert len(fake.tables["portal_sessions"]) == 1


def test_magic_link_is_single_use_and_replay_is_rejected() -> None:
    fake = _FakeSupabase()
    client = _client(fake)
    assert client.post("/auth/signup", json=_signup_payload()).status_code == 200
    token = _last_magic_token(client)

    first = client.get(f"/auth/callback?token={token}", follow_redirects=False)
    replay = client.get(f"/auth/callback?token={token}", follow_redirects=False)

    assert first.status_code == 303
    assert "/portal/sol-records#portal_token=" in first.headers["location"]
    assert replay.headers["location"] == "https://sunbeat.pro/login?error=used_link"
    assert len(fake.tables["portal_sessions"]) == 1


def test_magic_link_cannot_cross_workspace_membership() -> None:
    fake = _FakeSupabase()
    client = _client(fake)
    assert client.post("/auth/signup", json=_signup_payload()).status_code == 200
    token = _last_magic_token(client)
    fake.tables["workspace_users"][0]["workspace_slug"] = "another-workspace"

    response = client.get(f"/auth/callback?token={token}", follow_redirects=False)

    assert response.headers["location"] == "https://sunbeat.pro/login?error=workspace_access"
    assert fake.tables["self_service_magic_links"][0].get("consumed_at") is None


def test_logout_revokes_persistent_session() -> None:
    fake = _FakeSupabase()
    client = _client(fake)
    assert client.post("/auth/signup", json=_signup_payload()).status_code == 200
    callback = client.get(
        f"/auth/callback?token={_last_magic_token(client)}",
        follow_redirects=False,
    )
    portal_token = callback.headers["location"].split("#portal_token=", 1)[1]

    with patch.object(portal_session, "supabase", fake):
        assert portal_session.portal_token_is_valid(portal_token, "sol-records") is True
        logout = client.post("/auth/logout", headers={"X-Portal-Token": portal_token})
        assert logout.status_code == 200
        assert portal_session.portal_token_is_valid(portal_token, "sol-records") is False


def test_qa_mock_full_signup_onboarding_refresh_and_logout_journey() -> None:
    fake = _FakeSupabase()
    client = _client(fake)
    signup = client.post(
        "/auth/signup",
        json=_signup_payload(email="qa-owner@example.com", workspace_name="QA Isolated Records"),
        headers={"Host": "sunbeat.pro"},
    )
    assert signup.status_code == 200
    token = _last_magic_token(client)
    callback = client.get(f"/auth/callback?token={token}", follow_redirects=False)
    portal_token = callback.headers["location"].split("#portal_token=", 1)[1]
    headers = {"X-Portal-Token": portal_token}

    profile = {
        "operationType": "label",
        "teamSize": "2-5",
        "monthlyVolume": "11-50",
        "workflowTypes": ["release_intake"],
        "integrations": ["email"],
        "primaryGoal": "QA isolated onboarding",
    }
    with patch.object(onboarding, "supabase", fake), patch.object(portal_session, "supabase", fake):
        initial = client.get("/workspaces/qa-isolated-records/onboarding", headers=headers)
        assert initial.status_code == 200
        assert initial.json()["data"]["selfService"] is True
        preview = client.post(
            "/workspaces/qa-isolated-records/onboarding",
            headers=headers,
            json={"operation": "preview_patch", "profile": profile},
        ).json()["data"]
        apply_request = {
            "operation": "apply_patch",
            "profile": profile,
            "preview_token": preview["previewToken"],
        }
        applied = client.post(
            "/workspaces/qa-isolated-records/onboarding", headers=headers, json=apply_request
        )
        retried = client.post(
            "/workspaces/qa-isolated-records/onboarding", headers=headers, json=apply_request
        )
        refreshed = client.get("/workspaces/qa-isolated-records/onboarding", headers=headers)

        assert applied.status_code == 200
        assert retried.json()["data"]["status"] == "already_applied"
        assert refreshed.json()["data"]["completedAt"] == applied.json()["data"]["completedAt"]

        assert client.post("/auth/logout", headers=headers).status_code == 200
        after_logout = client.get("/workspaces/qa-isolated-records/onboarding", headers=headers)
        assert after_logout.status_code == 401


def test_invalid_callback_redirects_to_login() -> None:
    response = _client(_FakeSupabase()).get("/auth/callback?token=invalid", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "https://sunbeat.pro/login?error=invalid_link"


def test_login_unknown_email_is_neutral() -> None:
    response = _client(_FakeSupabase()).post(
        "/auth/magic-link",
        json={"email": "unknown@example.com", "company_website": ""},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_login_does_not_issue_link_when_owner_user_email_mismatches() -> None:
    fake = _FakeSupabase()
    fake.tables["workspaces"].append(
        {"slug": "sol-records", "name": "Sol Records", "owner_email": "owner@example.com"}
    )
    fake.tables["workspace_users"].append(
        {"workspace_slug": "sol-records", "user_id": "another-user", "role": "owner"}
    )
    client = _client(fake)

    response = client.post(
        "/auth/magic-link",
        json={"email": "owner@example.com", "company_website": ""},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert fake.tables["self_service_magic_links"] == []
    assert client._magic_email_mock.call_count == 0  # type: ignore[attr-defined]
