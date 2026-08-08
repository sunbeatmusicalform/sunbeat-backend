from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.services import asset_retention
from scripts.enforce_free_asset_retention import enforce_expired_assets


class _Query:
    def __init__(self, owner: "_FakeDatabase", name: str) -> None:
        self.owner = owner
        self.name = name
        self.operation = "select"
        self.payload: dict = {}
        self.filters: list[tuple[str, str, object]] = []
        self.max_rows: int | None = None

    def select(self, _fields: str) -> "_Query": return self
    def order(self, _field: str) -> "_Query": return self

    def limit(self, count: int) -> "_Query":
        self.max_rows = count
        return self

    def insert(self, payload: dict) -> "_Query":
        self.operation = "insert"
        self.payload = deepcopy(payload)
        return self

    def update(self, payload: dict) -> "_Query":
        self.operation = "update"
        self.payload = deepcopy(payload)
        return self

    def eq(self, key: str, value: object) -> "_Query":
        self.filters.append(("eq", key, value))
        return self

    def is_(self, key: str, value: object) -> "_Query":
        self.filters.append(("is", key, value))
        return self

    def lte(self, key: str, value: object) -> "_Query":
        self.filters.append(("lte", key, value))
        return self

    def _matched(self) -> list[dict]:
        rows = self.owner.tables.setdefault(self.name, [])
        matched = []
        for row in rows:
            ok = True
            for operation, key, value in self.filters:
                if operation == "eq" and row.get(key) != value:
                    ok = False
                elif operation == "is" and value == "null" and row.get(key) is not None:
                    ok = False
                elif operation == "lte" and str(row.get(key) or "") > str(value):
                    ok = False
            if ok:
                matched.append(row)
        return matched[: self.max_rows] if self.max_rows is not None else matched

    def execute(self) -> SimpleNamespace:
        if self.operation == "insert":
            self.owner.tables.setdefault(self.name, []).append(deepcopy(self.payload))
            return SimpleNamespace(data=[deepcopy(self.payload)])
        matched = self._matched()
        if self.operation == "update":
            for row in matched:
                row.update(deepcopy(self.payload))
        return SimpleNamespace(data=deepcopy(matched))


class _Bucket:
    def __init__(self, owner: "_FakeDatabase", name: str) -> None:
        self.owner = owner
        self.name = name

    def remove(self, paths: list[str]) -> None:
        for path in paths:
            key = (self.name, path)
            if key in self.owner.missing:
                raise RuntimeError("404 not found")
            if key in self.owner.failures:
                raise RuntimeError("storage unavailable")
            self.owner.removed.append(key)


class _Storage:
    def __init__(self, owner: "_FakeDatabase") -> None:
        self.owner = owner

    def from_(self, name: str) -> _Bucket:
        return _Bucket(self.owner, name)


class _FakeDatabase:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {"asset_retention_records": []}
        self.removed: list[tuple[str, str]] = []
        self.missing: set[tuple[str, str]] = set()
        self.failures: set[tuple[str, str]] = set()
        self.storage = _Storage(self)

    def table(self, name: str) -> _Query:
        return _Query(self, name)


def _due_record(*, asset_id: str = "asset-1", path: str = "qa/drafts/one/audio.wav") -> dict:
    return {
        "id": asset_id,
        "workspace_slug": "qa-free",
        "storage_bucket": "sunbeat-audio",
        "storage_path": path,
        "storage_status": "uploaded",
        "deletion_attempts": 0,
        "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "deleted_at": None,
    }


def test_free_asset_is_registered_with_60_day_expiry_and_hashed_draft_token() -> None:
    fake = _FakeDatabase()
    with (
        patch.object(asset_retention, "is_self_service_workspace", return_value=True),
        patch.object(
            asset_retention,
            "load_workspace_entitlements",
            return_value=SimpleNamespace(plan_id="free"),
        ),
    ):
        record = asset_retention.register_asset(
            workspace_slug="qa-free",
            draft_token="secret-draft-token",
            storage_bucket="sunbeat-audio",
            storage_path="qa/drafts/one/audio.wav",
            file_name="audio.wav",
            mime_type="audio/wav",
            size_bytes=123,
            status="uploaded",
            database=fake,
        )

    assert record is not None
    assert record["retention_days"] == 60
    assert record["draft_token_hash"] != "secret-draft-token"
    assert fake.tables["asset_retention_records"][0]["storage_status"] == "uploaded"


def test_managed_workspace_is_not_enrolled_in_free_retention() -> None:
    fake = _FakeDatabase()
    with patch.object(asset_retention, "is_self_service_workspace", return_value=False):
        record = asset_retention.register_asset(
            workspace_slug="atabaque",
            draft_token="token",
            storage_bucket="sunbeat-audio",
            storage_path="atabaque/drafts/one/audio.wav",
            file_name="audio.wav",
            mime_type="audio/wav",
            size_bytes=123,
            status="uploaded",
            database=fake,
        )

    assert record is None
    assert fake.tables["asset_retention_records"] == []


def test_expired_asset_access_is_denied_even_before_cleanup() -> None:
    fake = _FakeDatabase()
    fake.tables["asset_retention_records"].append(_due_record())

    with pytest.raises(HTTPException) as exc:
        asset_retention.assert_asset_not_expired(
            storage_bucket="sunbeat-audio",
            storage_path="qa/drafts/one/audio.wav",
            database=fake,
        )

    assert exc.value.status_code == 410


def test_cleanup_defaults_to_dry_run_and_is_idempotent() -> None:
    fake = _FakeDatabase()
    fake.tables["asset_retention_records"].append(_due_record())

    dry_run = enforce_expired_assets(database=fake)
    first_apply = enforce_expired_assets(database=fake, apply=True)
    second_apply = enforce_expired_assets(database=fake, apply=True)

    assert dry_run == {"eligible": 1, "deleted": 0, "missing": 0, "failed": 0}
    assert first_apply == {"eligible": 1, "deleted": 1, "missing": 0, "failed": 0}
    assert second_apply == {"eligible": 0, "deleted": 0, "missing": 0, "failed": 0}
    assert fake.removed == [("sunbeat-audio", "qa/drafts/one/audio.wav")]
    assert fake.tables["asset_retention_records"][0]["storage_status"] == "deleted"
    assert fake.tables["asset_retention_records"][0]["deleted_at"]


def test_cleanup_records_failure_without_marking_asset_deleted() -> None:
    fake = _FakeDatabase()
    record = _due_record()
    fake.tables["asset_retention_records"].append(record)
    fake.failures.add((record["storage_bucket"], record["storage_path"]))

    result = enforce_expired_assets(database=fake, apply=True)

    assert result["failed"] == 1
    assert record["storage_status"] == "error"
    assert record["deletion_attempts"] == 1
    assert record["deleted_at"] is None
