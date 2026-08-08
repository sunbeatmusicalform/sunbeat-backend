from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules import public_leads
from app.services import email as email_service


class _Query:
    def __init__(self, owner: "_FakeSupabase") -> None:
        self.owner = owner
        self.operation = "select"
        self.payload: dict = {}
        self.filters: list[tuple[str, object]] = []

    def insert(self, payload: dict) -> "_Query":
        self.operation = "insert"
        self.payload = deepcopy(payload)
        return self

    def update(self, payload: dict) -> "_Query":
        self.operation = "update"
        self.payload = deepcopy(payload)
        return self

    def eq(self, key: str, value: object) -> "_Query":
        self.filters.append((key, value))
        return self

    def execute(self) -> SimpleNamespace:
        if self.operation == "insert":
            self.owner.leads.append(deepcopy(self.payload))
            return SimpleNamespace(data=[deepcopy(self.payload)])
        matched = [
            row for row in self.owner.leads
            if all(row.get(key) == value for key, value in self.filters)
        ]
        if self.operation == "update":
            for row in matched:
                row.update(deepcopy(self.payload))
        return SimpleNamespace(data=deepcopy(matched))


class _FakeSupabase:
    def __init__(self, *, rate_allowed: bool = True) -> None:
        self.leads: list[dict] = []
        self.rate_allowed = rate_allowed

    def table(self, name: str) -> _Query:
        assert name == "public_leads"
        return _Query(self)

    def rpc(self, name: str, _payload: dict):
        assert name == "consume_public_rate_limit"
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.rate_allowed))


def _client(fake: _FakeSupabase | None = None) -> tuple[TestClient, _FakeSupabase]:
    database = fake or _FakeSupabase()
    app = FastAPI()
    app.include_router(public_leads.router)
    patcher = patch.object(public_leads, "supabase", database)
    patcher.start()
    client = TestClient(app)
    client._public_leads_patcher = patcher  # type: ignore[attr-defined]
    return client, database


def test_waitlist_lead_is_delivered_with_selected_plan() -> None:
    with patch.object(public_leads, "send_public_lead_email", return_value={"provider_message_id": "msg-1"}) as send:
        client, fake = _client()
        response = client.post(
            "/public/leads",
            json={
                "lead_type": "waitlist",
                "plan": "Starter",
                "name": "Ana Silva",
                "email": "ana@example.com",
                "company": "Aurora Records",
                "message": "Please let me know when it opens.",
            },
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["message_id"] == "msg-1"
    assert send.call_args.kwargs["plan"] == "Starter"
    assert send.call_args.kwargs["email"] == "ana@example.com"
    assert fake.leads[0]["delivery_status"] == "delivered"
    assert fake.leads[0]["provider_message_id"] == "msg-1"


def test_enterprise_lead_does_not_require_plan() -> None:
    with patch.object(public_leads, "send_public_lead_email", return_value={}) as send:
        response = _client()[0].post(
            "/public/leads",
            json={
                "lead_type": "enterprise",
                "name": "João Costa",
                "email": "joao@example.com",
            },
        )

    assert response.status_code == 200
    assert send.call_args.kwargs["lead_type"] == "enterprise"
    assert send.call_args.kwargs["plan"] is None


def test_academy_subscription_does_not_require_plan() -> None:
    with patch.object(public_leads, "send_public_lead_email", return_value={}) as send:
        response = _client()[0].post(
            "/public/leads",
            json={
                "lead_type": "academy",
                "name": "Ana Silva",
                "email": "ana@example.com",
                "message": "Sunbeat Academy subscription · en",
            },
        )

    assert response.status_code == 200
    assert send.call_args.kwargs["lead_type"] == "academy"
    assert send.call_args.kwargs["plan"] is None


def test_waitlist_requires_a_plan() -> None:
    with patch.object(public_leads, "send_public_lead_email") as send:
        response = _client()[0].post(
            "/public/leads",
            json={"lead_type": "waitlist", "name": "Ana Silva", "email": "ana@example.com"},
        )

    assert response.status_code == 422
    send.assert_not_called()


def test_honeypot_returns_success_without_sending_email() -> None:
    with patch.object(public_leads, "send_public_lead_email") as send:
        response = _client()[0].post(
            "/public/leads",
            json={
                "lead_type": "waitlist",
                "plan": "Free",
                "name": "Spam Bot",
                "email": "bot@example.com",
                "website": "https://spam.example.com",
            },
        )

    assert response.status_code == 200
    send.assert_not_called()


def test_delivery_failure_is_persisted_and_not_reported_as_success() -> None:
    client, fake = _client()
    with patch.object(public_leads, "send_public_lead_email", side_effect=RuntimeError("provider down")):
        response = client.post(
            "/public/leads",
            json={
                "lead_type": "enterprise",
                "name": "Ana Silva",
                "email": "ana@example.com",
            },
        )

    assert response.status_code == 502
    assert fake.leads[0]["delivery_status"] == "failed"
    assert fake.leads[0]["delivery_error"] == "RuntimeError"


def test_rate_limit_is_shared_and_fail_closed() -> None:
    client, fake = _client(_FakeSupabase(rate_allowed=False))
    with patch.object(public_leads, "send_public_lead_email") as send:
        response = client.post(
            "/public/leads",
            json={
                "lead_type": "enterprise",
                "name": "Ana Silva",
                "email": "ana@example.com",
            },
        )

    assert response.status_code == 429
    assert fake.leads == []
    send.assert_not_called()


def test_public_lead_delivery_targets_confirmed_inbox() -> None:
    with patch.object(
        email_service,
        "_post_resend",
        return_value={"provider_message_id": "msg-1"},
    ) as post:
        email_service.send_public_lead_email(
            lead_type="enterprise",
            name="Ana Silva",
            email="ana@example.com",
            company="Aurora Records",
            message="Hello",
        )

    assert post.call_args.kwargs["to_email"] == "contatofelipefonsek@gmail.com"
