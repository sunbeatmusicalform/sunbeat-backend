from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

from app.services import automation_outbox


class FakeTableQuery:
    def __init__(self, records):
        self.records = records
        self.operation = None
        self.value = None
        self.filters = []

    def insert(self, value):
        self.operation = "insert"
        self.value = dict(value)
        return self

    def select(self, _fields):
        self.operation = "select"
        return self

    def update(self, value):
        self.operation = "update"
        self.value = dict(value)
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def limit(self, _limit):
        return self

    def execute(self):
        matching = [
            row for row in self.records
            if all(row.get(field) == expected for field, expected in self.filters)
        ]
        if self.operation == "insert":
            if any(row.get("idempotency_key") == self.value.get("idempotency_key") for row in self.records):
                raise RuntimeError("duplicate key")
            created = dict(self.value)
            self.records.append(created)
            return SimpleNamespace(data=[created])
        if self.operation == "update":
            for row in matching:
                row.update(self.value)
            return SimpleNamespace(data=matching)
        return SimpleNamespace(data=matching)


class FakeSupabase:
    def __init__(self):
        self.records = []

    def table(self, name):
        assert name == "automation_outbox"
        return FakeTableQuery(self.records)


def configure_enabled(monkeypatch, workspace="sunbeat-qa"):
    monkeypatch.setattr(automation_outbox.settings, "ACTIVEPIECES_ENABLED", True)
    monkeypatch.setattr(automation_outbox.settings, "ACTIVEPIECES_WORKSPACE_ALLOWLIST", workspace)
    monkeypatch.setattr(automation_outbox.settings, "ACTIVEPIECES_WORKSPACE_DENYLIST", "atabaque")
    monkeypatch.setattr(automation_outbox.settings, "ACTIVEPIECES_WEBHOOK_URL", "https://automation.example.test/hook")
    monkeypatch.setattr(automation_outbox.settings, "ACTIVEPIECES_WEBHOOK_SECRET", "test-secret")


def test_workspace_must_be_enabled_allowlisted_and_not_denied(monkeypatch):
    configure_enabled(monkeypatch)

    assert automation_outbox.automation_enabled_for_workspace("sunbeat-qa") is True
    assert automation_outbox.automation_enabled_for_workspace("another-tenant") is False
    assert automation_outbox.automation_workspace_status("atabaque")["status"] == "blocked"


def test_atabaque_remains_denied_if_environment_denylist_is_empty(monkeypatch):
    configure_enabled(monkeypatch, workspace="atabaque")
    monkeypatch.setattr(automation_outbox.settings, "ACTIVEPIECES_WORKSPACE_DENYLIST", "")

    assert automation_outbox.automation_workspace_status("atabaque")["status"] == "blocked"


def test_enqueue_is_idempotent_by_workspace_event_and_entity(monkeypatch):
    configure_enabled(monkeypatch)
    fake = FakeSupabase()
    monkeypatch.setattr(automation_outbox, "supabase", fake)

    kwargs = {
        "workspace_slug": "sunbeat-qa",
        "event_type": "submission.created",
        "entity_type": "submission",
        "entity_id": "submission-1",
        "payload": {"project_title": "Projeto QA"},
    }
    first = automation_outbox.enqueue_event(**kwargs)
    replay = automation_outbox.enqueue_event(**kwargs)

    assert first["status"] == "queued"
    assert first["queued"] is True
    assert first["event_id"] == fake.records[0]["id"]
    assert replay["queued"] is True
    assert replay["replayed"] is True
    assert replay["event_id"] == first["event_id"]
    assert len(fake.records) == 1
    assert fake.records[0]["payload"]["data"] == {"project_title": "Projeto QA"}
    assert fake.records[0]["payload"]["event_id"] == fake.records[0]["id"]


def test_delivery_signs_canonical_body_and_marks_delivered(monkeypatch):
    configure_enabled(monkeypatch)
    fake = FakeSupabase()
    fake.records.append({"id": "event-1", "status": "sending"})
    monkeypatch.setattr(automation_outbox, "supabase", fake)
    captured = {}

    def post(url, *, data, headers, timeout):
        captured.update({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return SimpleNamespace(status_code=202)

    monkeypatch.setattr(automation_outbox.requests, "post", post)
    row = {
        "id": "event-1",
        "attempts": 1,
        "payload": {"event_id": "public-event-id", "data": {"title": "Canção"}},
    }

    result = automation_outbox.deliver_claimed_event(row)

    body = captured["data"].decode("utf-8")
    timestamp = captured["headers"]["X-Sunbeat-Timestamp"]
    expected = hmac.new(
        b"test-secret",
        f"{timestamp}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert json.loads(body) == row["payload"]
    assert captured["headers"]["X-Sunbeat-Signature"] == f"sha256={expected}"
    assert captured["headers"]["Idempotency-Key"] == "event-1"
    assert result["status"] == "delivered"
    assert fake.records[0]["status"] == "delivered"


def test_invalid_non_https_webhook_is_rejected(monkeypatch):
    configure_enabled(monkeypatch)
    monkeypatch.setattr(automation_outbox.settings, "ACTIVEPIECES_WEBHOOK_URL", "http://public.example/hook")

    try:
        automation_outbox._validated_webhook_url()
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("public HTTP webhook should be rejected")
