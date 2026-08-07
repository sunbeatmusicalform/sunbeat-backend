from __future__ import annotations

import os
import sys
import time
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
from app.modules import self_service_auth


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

    def delete(self) -> "_Query":
        self.operation = "delete"
        return self

    def _matched(self) -> list[dict]:
        return [row for row in self.owner.tables.setdefault(self.name, []) if all(row.get(k) == v for k, v in self.filters)]

    def execute(self) -> SimpleNamespace:
        rows = self.owner.tables.setdefault(self.name, [])
        if self.operation == "insert":
            rows.append(deepcopy(self.payload or {}))
            return SimpleNamespace(data=[deepcopy(self.payload or {})])
        if self.operation == "delete":
            matched = self._matched()
            self.owner.tables[self.name] = [row for row in rows if row not in matched]
            return SimpleNamespace(data=deepcopy(matched))
        return SimpleNamespace(data=deepcopy(self._matched()))


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


class _FakeSupabase:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {
            "workspaces": [], "workspace_users": [], "workspace_branding": [],
        }
        self.admin = _Admin()
        self.auth = SimpleNamespace(admin=self.admin)

    def table(self, name: str) -> _Query:
        return _Query(self, name)


def _client(fake: _FakeSupabase) -> TestClient:
    settings.INTERNAL_ADMIN_TOKEN = "test-admin-token"
    settings.SELF_SERVICE_SIGNUP_ENABLED = True
    settings.FRONTEND_BASE_URL = "https://sunbeat.pro"
    self_service_auth._attempts.clear()
    patches = (
        patch.object(self_service_auth, "supabase", fake),
        patch.object(self_service_auth, "send_workspace_magic_link_email", return_value={"provider": "test"}),
    )
    for item in patches: item.start()
    client = TestClient(app)
    client._self_service_patches = patches  # type: ignore[attr-defined]
    return client


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
    token = self_service_auth._issue_magic_token(
        user_id="11111111-1111-1111-1111-111111111111",
        workspace_slug="sol-records",
    )
    response = client.get(f"/auth/callback?token={token}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("https://sunbeat.pro/portal/sol-records#portal_token=")
    assert fake.admin.updated == [
        ("11111111-1111-1111-1111-111111111111", {"email_confirm": True})
    ]


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
