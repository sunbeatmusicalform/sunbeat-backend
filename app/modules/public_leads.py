from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from app.core.database import supabase
from app.services.email import send_public_lead_email
from app.services.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/public", tags=["public"])
logger = logging.getLogger("sunbeat.public_leads")

_RATE_WINDOW_SECONDS = 10 * 60
_RATE_LIMIT = 5


class PublicLeadRequest(BaseModel):
    lead_type: Literal["waitlist", "enterprise", "academy"]
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    company: str | None = Field(default=None, max_length=160)
    plan: Literal["Free", "Starter", "Pro"] | None = None
    message: str | None = Field(default=None, max_length=2000)
    website: str | None = Field(default=None, max_length=200)


def _locale(request: Request) -> str:
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").lower()
    return "pt-BR" if host.split(":", 1)[0].endswith("sunbeat.com.br") else "en"


@router.post("/leads")
def create_public_lead(payload: PublicLeadRequest, request: Request):
    # Honeypot: bots receive a neutral success without generating email.
    if payload.website:
        return {"ok": True}

    email = str(payload.email).strip().lower()
    enforce_rate_limit(
        request,
        scope=f"public-lead:{payload.lead_type}:ip",
        limit=20,
        window_seconds=_RATE_WINDOW_SECONDS,
        database=supabase,
    )
    enforce_rate_limit(
        request,
        scope=f"public-lead:{payload.lead_type}:subject",
        limit=_RATE_LIMIT,
        window_seconds=_RATE_WINDOW_SECONDS,
        subject=email,
        database=supabase,
    )

    if payload.lead_type == "waitlist" and not payload.plan:
        raise HTTPException(status_code=422, detail="Plan is required for waitlist submissions.")

    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Name is required.")

    lead_id = str(uuid.uuid4())
    lead_record = {
        "id": lead_id,
        "lead_type": payload.lead_type,
        "name": name,
        "email": email,
        "company": (payload.company or "").strip() or None,
        "plan": payload.plan,
        "message": (payload.message or "").strip() or None,
        "locale": _locale(request),
        "delivery_status": "received",
    }
    delivery_recorded = True
    try:
        supabase.table("public_leads").insert(lead_record).execute()
    except Exception as exc:
        logger.exception("Failed to persist public lead id=%s", lead_id)
        raise HTTPException(status_code=503, detail="Could not save your message. Please try again.") from exc

    try:
        result = send_public_lead_email(
            lead_type=payload.lead_type,
            name=name,
            email=email,
            company=lead_record["company"],
            plan=payload.plan,
            message=lead_record["message"],
        )
    except Exception as exc:
        try:
            (
                supabase.table("public_leads")
                .update({"delivery_status": "failed", "delivery_error": type(exc).__name__})
                .eq("id", lead_id)
                .execute()
            )
        except Exception:
            logger.exception("Failed to record public lead delivery failure id=%s", lead_id)
        logger.exception("Failed to deliver public lead id=%s: %s", lead_id, exc)
        raise HTTPException(status_code=502, detail="Could not deliver your message. Please try again.") from exc

    message_id = result.get("provider_message_id")
    try:
        (
            supabase.table("public_leads")
            .update(
                {
                    "delivery_status": "delivered",
                    "provider_message_id": message_id,
                    "delivered_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", lead_id)
            .execute()
        )
    except Exception as exc:
        logger.exception("Failed to confirm public lead delivery id=%s", lead_id)
        delivery_recorded = False

    return {
        "ok": True,
        "lead_id": lead_id,
        "message_id": message_id,
        "delivery_recorded": delivery_recorded,
    }
