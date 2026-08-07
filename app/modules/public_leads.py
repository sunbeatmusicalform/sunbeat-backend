from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from app.services.email import send_public_lead_email

router = APIRouter(prefix="/public", tags=["public"])
logger = logging.getLogger("sunbeat.public_leads")

_attempts: dict[str, deque[float]] = defaultdict(deque)
_RATE_WINDOW_SECONDS = 60 * 10
_RATE_LIMIT = 5


class PublicLeadRequest(BaseModel):
    lead_type: Literal["waitlist", "enterprise", "academy"]
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    company: str | None = Field(default=None, max_length=160)
    plan: Literal["Free", "Starter", "Pro"] | None = None
    message: str | None = Field(default=None, max_length=2000)
    website: str | None = Field(default=None, max_length=200)


def _check_rate_limit(client_key: str) -> None:
    now = time.monotonic()
    recent = _attempts[client_key]
    while recent and now - recent[0] > _RATE_WINDOW_SECONDS:
        recent.popleft()
    if len(recent) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
    recent.append(now)


@router.post("/leads")
def create_public_lead(payload: PublicLeadRequest, request: Request):
    # Honeypot: bots receive a neutral success without generating email.
    if payload.website:
        return {"ok": True}

    client_key = request.client.host if request.client else "unknown"
    _check_rate_limit(client_key)

    if payload.lead_type == "waitlist" and not payload.plan:
        raise HTTPException(status_code=422, detail="Plan is required for waitlist submissions.")

    try:
        result = send_public_lead_email(
            lead_type=payload.lead_type,
            name=payload.name.strip(),
            email=str(payload.email),
            company=(payload.company or "").strip() or None,
            plan=payload.plan,
            message=(payload.message or "").strip() or None,
        )
    except Exception as exc:
        logger.exception("Failed to deliver public lead: %s", exc)
        raise HTTPException(status_code=502, detail="Could not deliver your message. Please try again.") from exc

    return {"ok": True, "message_id": result.get("provider_message_id")}
