from __future__ import annotations

from hmac import compare_digest
from typing import Optional

from fastapi import Header, HTTPException

from app.core.config import settings


def _configured_admin_tokens() -> list[str]:
    candidates = [
        settings.INTERNAL_ADMIN_TOKEN,
        settings.AI_COPILOT_SECRET,
    ]
    return [
        value.strip()
        for value in candidates
        if isinstance(value, str) and value.strip()
    ]


def _admin_token_is_valid(incoming: str) -> bool:
    expected_tokens = _configured_admin_tokens()
    return bool(
        expected_tokens
        and incoming
        and any(compare_digest(incoming, expected) for expected in expected_tokens)
    )


def _bearer_token(authorization: Optional[str]) -> str:
    if not isinstance(authorization, str):
        return ""
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


def _supabase_user_token_is_valid(token: str) -> bool:
    if not token:
        return False
    try:
        from app.core.database import supabase

        response = supabase.auth.get_user(token)
        user = getattr(response, "user", None)
        return bool(user and getattr(user, "id", None))
    except Exception:
        return False


async def require_admin_token(
    x_admin_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> None:
    incoming = x_admin_token.strip() if isinstance(x_admin_token, str) else ""
    if _admin_token_is_valid(incoming):
        return

    if _supabase_user_token_is_valid(_bearer_token(authorization)):
        return

    raise HTTPException(status_code=401, detail="unauthorized")


async def require_internal_admin_token(
    x_admin_token: Optional[str] = Header(default=None),
) -> None:
    incoming = x_admin_token.strip() if isinstance(x_admin_token, str) else ""
    if _admin_token_is_valid(incoming):
        return

    raise HTTPException(status_code=401, detail="unauthorized")
