from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules import public_leads


def _client() -> TestClient:
    public_leads._attempts.clear()
    app = FastAPI()
    app.include_router(public_leads.router)
    return TestClient(app)


def test_waitlist_lead_is_delivered_with_selected_plan() -> None:
    with patch.object(public_leads, "send_public_lead_email", return_value={"provider_message_id": "msg-1"}) as send:
        response = _client().post(
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
    assert response.json() == {"ok": True, "message_id": "msg-1"}
    assert send.call_args.kwargs["plan"] == "Starter"
    assert send.call_args.kwargs["email"] == "ana@example.com"


def test_enterprise_lead_does_not_require_plan() -> None:
    with patch.object(public_leads, "send_public_lead_email", return_value={}) as send:
        response = _client().post(
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
        response = _client().post(
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
        response = _client().post(
            "/public/leads",
            json={"lead_type": "waitlist", "name": "Ana Silva", "email": "ana@example.com"},
        )

    assert response.status_code == 422
    send.assert_not_called()


def test_honeypot_returns_success_without_sending_email() -> None:
    with patch.object(public_leads, "send_public_lead_email") as send:
        response = _client().post(
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
