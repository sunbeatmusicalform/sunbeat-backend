from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from app.core.config import settings


async def require_admin_token(
    x_admin_token: Optional[str] = Header(default=None),
) -> None:
    expected = settings.INTERNAL_ADMIN_TOKEN
    if not expected or not x_admin_token or x_admin_token != expected:
        raise HTTPException(status_code=401, detail="unauthorized")
