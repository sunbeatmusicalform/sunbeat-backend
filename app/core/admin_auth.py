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


async def require_admin_token(
    x_admin_token: Optional[str] = Header(default=None),
) -> None:
    expected_tokens = _configured_admin_tokens()
    incoming = x_admin_token.strip() if isinstance(x_admin_token, str) else ""
    if (
        not expected_tokens
        or not incoming
        or not any(compare_digest(incoming, expected) for expected in expected_tokens)
    ):
        raise HTTPException(status_code=401, detail="unauthorized")
