"""
portal_session.py
Sessão do portal do cliente (por workspace) — credencial de primeiro nível.

Fluxo v1:
  POST /workspaces/{workspace_slug}/portal-session  {"password": "..."}
    -> compara SHA-256 da senha com settings.PORTAL_PASS_SHA256
    -> retorna token HMAC assinado com INTERNAL_ADMIN_TOKEN (expira em 12h)

O token de portal habilita endpoints do tenant sem expor o admin token:
  PATCH /workspaces/{workspace_slug}/branding   (marca do tenant)
  GET/PATCH drive-config                        (aceito como credencial alternativa)

Fase 2 substitui por Supabase Auth por usuário/tenant.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["portal-session"])

TOKEN_TTL_SECONDS = 12 * 60 * 60  # 12h


class PortalSessionRequest(BaseModel):
    password: str


def _signing_key() -> str:
    key = getattr(settings, "INTERNAL_ADMIN_TOKEN", None) or ""
    return key.strip()


def _expected_pass_sha256() -> str:
    value = getattr(settings, "PORTAL_PASS_SHA256", None) or ""
    return value.strip().lower()


def issue_portal_token(
    workspace_slug: str,
    expires_at: Optional[int] = None,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    exp = expires_at or int(time.time()) + TOKEN_TTL_SECONDS
    claims = {"ws": workspace_slug, "exp": exp}
    if user_id or session_id:
        if not user_id or not session_id:
            raise ValueError("user_id and session_id must be provided together")
        claims.update({"uid": user_id, "sid": session_id})
    payload = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    sig = hmac.new(_signing_key().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _decode_portal_token(token: str, *, allow_expired: bool = False) -> Optional[dict]:
    if not token or "." not in token:
        return None
    payload, _, sig = token.rpartition(".")
    key = _signing_key()
    if not key:
        return None
    expected = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return None
    if not allow_expired and int(data.get("exp", 0)) <= int(time.time()):
        return None
    return data


def _persistent_session_is_valid(data: dict) -> bool:
    session_id = str(data.get("sid") or "")
    user_id = str(data.get("uid") or "")
    workspace_slug = str(data.get("ws") or "")
    if bool(session_id) != bool(user_id):
        return False
    if not session_id:
        # Backward compatibility for managed/password sessions. Self-service
        # sessions always carry both claims and are checked persistently.
        return True
    try:
        rows = (
            supabase.table("portal_sessions")
            .select("session_id,user_id,workspace_slug,expires_at,revoked_at")
            .eq("session_id", session_id)
            .eq("user_id", user_id)
            .eq("workspace_slug", workspace_slug)
            .limit(1)
            .execute()
        )
        row = (getattr(rows, "data", None) or [None])[0]
        if not row or row.get("revoked_at"):
            return False
        expires_at = datetime.fromisoformat(str(row.get("expires_at") or "").replace("Z", "+00:00"))
        if expires_at <= datetime.now(timezone.utc):
            return False
        membership = (
            supabase.table("workspace_users")
            .select("user_id")
            .eq("workspace_slug", workspace_slug)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        return bool(getattr(membership, "data", None))
    except Exception:
        logger.exception("persistent portal session validation failed workspace=%s", workspace_slug)
        return False


def portal_token_is_valid(token: str, workspace_slug: str) -> bool:
    data = _decode_portal_token(token)
    if not data or data.get("ws") != workspace_slug:
        return False
    return _persistent_session_is_valid(data)


def require_portal_session(workspace_slug: str, x_portal_token: Optional[str]) -> None:
    """401 — nunca 422 — quando o token de portal está ausente ou inválido."""
    token = (x_portal_token or "").strip()
    if not portal_token_is_valid(token, workspace_slug):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/{workspace_slug}/portal-session")
async def create_portal_session(workspace_slug: str, body: PortalSessionRequest):
    expected = _expected_pass_sha256()
    if not expected:
        raise HTTPException(status_code=503, detail="portal password not configured")
    incoming = hashlib.sha256(body.password.encode()).hexdigest().lower()
    if not hmac.compare_digest(incoming, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"ok": True, "workspace_slug": workspace_slug, "token": issue_portal_token(workspace_slug)}
