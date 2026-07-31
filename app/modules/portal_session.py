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
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.core.config import settings

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


def issue_portal_token(workspace_slug: str, expires_at: Optional[int] = None) -> str:
    exp = expires_at or int(time.time()) + TOKEN_TTL_SECONDS
    payload = base64.urlsafe_b64encode(
        json.dumps({"ws": workspace_slug, "exp": exp}, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    sig = hmac.new(_signing_key().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def portal_token_is_valid(token: str, workspace_slug: str) -> bool:
    if not token or "." not in token:
        return False
    payload, _, sig = token.rpartition(".")
    key = _signing_key()
    if not key:
        return False
    expected = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return False
    if data.get("ws") != workspace_slug:
        return False
    return int(data.get("exp", 0)) > int(time.time())


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
