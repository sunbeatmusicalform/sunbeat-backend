from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")

from app.services import self_service_entitlements


class _Query:
    def __init__(self, result):
        self.result = result

    def select(self, *_args, **_kwargs): return self
    def eq(self, *_args, **_kwargs): return self
    def limit(self, *_args, **_kwargs): return self
    @property
    def not_(self): return self
    def is_(self, *_args, **_kwargs): return self
    def gte(self, *_args, **_kwargs): return self
    def execute(self): return self.result


class _Admin:
    def __init__(self, self_service):
        self.self_service = self_service

    def get_user_by_id(self, _user_id):
        return SimpleNamespace(
            user=SimpleNamespace(user_metadata={"self_service": self.self_service})
        )


class _Supabase:
    def __init__(self, results, self_service=True):
        self.results = {name: list(values) for name, values in results.items()}
        self.auth = SimpleNamespace(admin=_Admin(self_service))

    def table(self, name): return _Query(self.results[name].pop(0))


def _result(data=None, count=None): return SimpleNamespace(data=data or [], count=count)


def test_legacy_workspace_is_never_limited(monkeypatch):
    fake = _Supabase(
        {"workspace_users": [_result([{"user_id": "legacy-owner"}])]},
        self_service=False,
    )
    monkeypatch.setattr(self_service_entitlements, "supabase", fake)

    self_service_entitlements.enforce_self_service_submission_limits(
        workspace_slug="atabaque", workflow_type="rights_clearance"
    )


def test_free_self_service_blocks_workflow_outside_plan(monkeypatch):
    fake = _Supabase({
        "workspace_users": [_result([{"user_id": "owner"}])],
        "workspaces": [_result([{"plan_id": "free", "plans": {"submissions_month": 50}}])],
        "workspace_plan_overrides": [_result([])],
    })
    monkeypatch.setattr(self_service_entitlements, "supabase", fake)

    with pytest.raises(HTTPException) as exc:
        self_service_entitlements.enforce_self_service_submission_limits(
            workspace_slug="demo", workflow_type="rights_clearance"
        )
    assert exc.value.status_code == 403


def test_free_self_service_blocks_monthly_limit(monkeypatch):
    fake = _Supabase({
        "workspace_users": [_result([{"user_id": "owner"}])],
        "workspaces": [_result([{"plan_id": "free", "plans": {"submissions_month": 50}}])],
        "workspace_plan_overrides": [_result([])],
        "submissions": [_result(count=50)],
    })
    monkeypatch.setattr(self_service_entitlements, "supabase", fake)

    with pytest.raises(HTTPException) as exc:
        self_service_entitlements.enforce_self_service_submission_limits(
            workspace_slug="demo", workflow_type="release_intake"
        )
    assert exc.value.status_code == 429


def test_workspace_override_resolves_custom_workflows_and_limit(monkeypatch):
    fake = _Supabase({
        "workspaces": [_result([{"plan_id": "free", "plans": {"submissions_month": 50}}])],
        "workspace_plan_overrides": [_result([{
            "max_submissions_month": 500,
            "enabled_workflow_types": ["release_intake", "rights_clearance"],
        }])],
    })
    monkeypatch.setattr(self_service_entitlements, "supabase", fake)

    resolved = self_service_entitlements.load_workspace_entitlements("demo")

    assert resolved.plan_id == "free"
    assert resolved.access_mode == "custom"
    assert resolved.max_submissions_month == 500
    assert resolved.enabled_workflow_types == {"release_intake", "rights_clearance"}
