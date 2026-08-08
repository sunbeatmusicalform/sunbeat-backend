"""Persistent, privacy-preserving rate limiting for public endpoints."""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from fastapi import HTTPException, Request

from app.core.config import settings
from app.core.database import supabase

logger = logging.getLogger("sunbeat.rate_limit")


def _client_address(request: Request) -> str:
    # Fly-Client-IP is set by Fly Proxy. Do not trust the user-controlled
    # X-Forwarded-For header for an abuse-control identity.
    return (
        request.headers.get("fly-client-ip")
        or (request.client.host if request.client else "unknown")
    ).strip()


def _identifier(request: Request, subject: str | None) -> str:
    key = (settings.INTERNAL_ADMIN_TOKEN or settings.AI_COPILOT_SECRET or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="Abuse protection is unavailable.")
    value = f"{_client_address(request)}:{(subject or '').strip().lower()}"
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


def enforce_rate_limit(
    request: Request,
    *,
    scope: str,
    limit: int,
    window_seconds: int,
    subject: str | None = None,
    database: Any = None,
) -> None:
    """Consume one attempt through an atomic database function.

    The endpoint fails closed when the shared limiter is unavailable. This
    prevents a restart or a second Fly machine from resetting abuse counters.
    """
    client = database or supabase
    try:
        result = client.rpc(
            "consume_public_rate_limit",
            {
                "p_scope": scope,
                "p_identifier_hash": _identifier(request, subject),
                "p_limit": limit,
                "p_window_seconds": window_seconds,
            },
        ).execute()
        allowed = getattr(result, "data", None)
        if isinstance(allowed, list):
            allowed = allowed[0] if allowed else False
        if isinstance(allowed, dict):
            allowed = allowed.get("allowed")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("persistent rate limiter unavailable scope=%s", scope)
        raise HTTPException(status_code=503, detail="Abuse protection is unavailable.") from exc
    if allowed is not True:
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
