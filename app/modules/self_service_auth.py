"""Self-service access hosted entirely by the FastAPI/Fly application."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from app.core.config import settings
from app.core.database import supabase
from app.modules.portal_session import TOKEN_TTL_SECONDS, _decode_portal_token, issue_portal_token
from app.services.email import send_workspace_magic_link_email
from app.services.rate_limit import enforce_rate_limit

logger = logging.getLogger("sunbeat.self_service_auth")
router = APIRouter(prefix="/auth", tags=["self-service-auth"])

MAGIC_LINK_TTL_SECONDS = 30 * 60
TERMS_VERSION = "sunbeat-terms-2026-08-07"
PRIVACY_VERSION = "sunbeat-privacy-2026-08-07"
RESERVED_SLUGS = {"admin", "api", "app", "academy", "help", "mail", "start", "status", "support", "www"}


class SignupRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    workspace_name: str = Field(min_length=2, max_length=120)
    plan_intent: Optional[str] = None
    terms_accepted: bool
    company_website: Optional[str] = Field(default=None, max_length=200)
    form_started_at: int


class LoginRequest(BaseModel):
    email: EmailStr
    company_website: Optional[str] = Field(default=None, max_length=200)


def _first_row(result: Any) -> Optional[dict[str, Any]]:
    rows = getattr(result, "data", None) or []
    return rows[0] if rows else None


def _rate_limit(request: Request, scope: str, *, subject: str, limit: int = 5) -> None:
    enforce_rate_limit(
        request,
        scope=f"auth:{scope}:ip",
        limit=20,
        window_seconds=10 * 60,
        database=supabase,
    )
    enforce_rate_limit(
        request,
        scope=f"auth:{scope}:subject",
        limit=limit,
        window_seconds=10 * 60,
        subject=subject,
        database=supabase,
    )


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.lower())
    ascii_value = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", ascii_value))[:32]


def _origin(request: Request) -> str:
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0].lower()
    if host.endswith("sunbeat.com.br"):
        return "https://sunbeat.com.br"
    if host.endswith("sunbeat.pro"):
        return "https://sunbeat.pro"
    return settings.FRONTEND_BASE_URL.rstrip("/")


def _locale(request: Request) -> str:
    return "pt-BR" if _origin(request).endswith(".com.br") else "en"


def _magic_key() -> str:
    key = (settings.INTERNAL_ADMIN_TOKEN or settings.AI_COPILOT_SECRET or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="Magic-link signing is unavailable.")
    return key


def _issue_magic_token(*, user_id: str, workspace_slug: str, token_id: Optional[str] = None) -> str:
    jti = token_id or str(uuid.uuid4())
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "uid": user_id,
                "ws": workspace_slug,
                "jti": jti,
                "exp": int(time.time()) + MAGIC_LINK_TTL_SECONDS,
            },
            separators=(",", ":"),
        ).encode()
    ).decode().rstrip("=")
    signature = hmac.new(_magic_key().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _verify_magic_token(token: str) -> Optional[dict[str, Any]]:
    payload, separator, signature = (token or "").rpartition(".")
    if not separator:
        return None
    expected = hmac.new(_magic_key().encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padded = payload + "=" * (-len(payload) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return None
    if (
        not value.get("uid")
        or not value.get("ws")
        or not value.get("jti")
        or int(value.get("exp") or 0) <= int(time.time())
    ):
        return None
    return value


def _delete_rows(table: str, **filters: str) -> None:
    query = supabase.table(table).delete()
    for key, value in filters.items():
        query = query.eq(key, value)
    query.execute()


def _rollback(user_id: str, workspace_slug: str) -> None:
    for table, filters in (
        ("self_service_magic_links", {"workspace_slug": workspace_slug, "user_id": user_id}),
        ("workspace_branding", {"workspace_slug": workspace_slug}),
        ("workspace_users", {"workspace_slug": workspace_slug, "user_id": user_id}),
        ("workspaces", {"slug": workspace_slug}),
    ):
        try:
            _delete_rows(table, **filters)
        except Exception:
            logger.exception("signup rollback failed table=%s workspace=%s", table, workspace_slug)
    try:
        supabase.auth.admin.delete_user(user_id)
    except Exception:
        logger.exception("signup rollback failed user=%s", user_id)


def _magic_link(
    request: Request,
    *,
    user_id: str,
    workspace_slug: str,
    purpose: str,
) -> str:
    token_id = str(uuid.uuid4())
    token = _issue_magic_token(user_id=user_id, workspace_slug=workspace_slug, token_id=token_id)
    supabase.table("self_service_magic_links").insert(
        {
            "token_id": token_id,
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "user_id": user_id,
            "workspace_slug": workspace_slug,
            "purpose": purpose,
            "expires_at": datetime.fromtimestamp(
                int(time.time()) + MAGIC_LINK_TTL_SECONDS, timezone.utc
            ).isoformat(),
        }
    ).execute()
    query = urlencode({"token": token})
    return f"{_origin(request)}/auth/callback?{query}"


def _consume_magic_link(token: str, value: dict[str, Any]) -> bool:
    try:
        result = supabase.rpc(
            "consume_self_service_magic_link",
            {
                "p_token_id": str(value["jti"]),
                "p_token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "p_user_id": str(value["uid"]),
                "p_workspace_slug": str(value["ws"]),
            },
        ).execute()
        consumed = getattr(result, "data", None)
        if isinstance(consumed, list):
            consumed = consumed[0] if consumed else False
        return consumed is True
    except Exception:
        logger.exception("magic-link consumption failed workspace=%s", value.get("ws"))
        return False


def _issue_persistent_portal_session(*, user_id: str, workspace_slug: str) -> str:
    session_id = str(uuid.uuid4())
    expires_at = int(time.time()) + TOKEN_TTL_SECONDS
    supabase.table("portal_sessions").insert(
        {
            "session_id": session_id,
            "user_id": user_id,
            "workspace_slug": workspace_slug,
            "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
        }
    ).execute()
    return issue_portal_token(
        workspace_slug,
        expires_at=expires_at,
        user_id=user_id,
        session_id=session_id,
    )


@router.post("/signup")
def signup(payload: SignupRequest, request: Request):
    if not settings.SELF_SERVICE_SIGNUP_ENABLED:
        raise HTTPException(status_code=503, detail="New signups are temporarily closed.")
    if payload.company_website:
        return {"ok": True, "requires_email_confirmation": True}
    email = str(payload.email).strip().lower()
    _rate_limit(request, "signup", subject=email)
    elapsed = int(time.time() * 1000) - payload.form_started_at
    if elapsed < 1200 or elapsed > 2 * 60 * 60 * 1000:
        raise HTTPException(status_code=422, detail="The form could not be validated. Reload and try again.")
    if payload.terms_accepted is not True:
        raise HTTPException(status_code=422, detail="Accept the Terms of Use and Privacy Policy.")

    workspace_slug = _slugify(payload.workspace_name)
    if len(workspace_slug) < 2:
        raise HTTPException(status_code=422, detail="Invalid workspace name.")
    if workspace_slug in RESERVED_SLUGS:
        raise HTTPException(status_code=409, detail="This workspace address is reserved.")
    if _first_row(supabase.table("workspaces").select("slug").eq("slug", workspace_slug).limit(1).execute()):
        raise HTTPException(status_code=409, detail="This workspace address is already in use.")

    accepted_at = datetime.now(timezone.utc).isoformat()
    try:
        auth_result = supabase.auth.admin.create_user(
            {
                "email": email,
                "password": secrets.token_urlsafe(48),
                "email_confirm": False,
                "app_metadata": {
                    # Authorization attributes must be server-controlled. Supabase
                    # users can edit user_metadata themselves.
                    "self_service": True,
                    "asset_retention_days": 60,
                },
                "user_metadata": {
                    "full_name": payload.name.strip(),
                    "workspace_slug": workspace_slug,
                    "signup_market": "brazil" if _locale(request) == "pt-BR" else "global",
                    "terms_accepted_at": accepted_at,
                    "terms_version": TERMS_VERSION,
                    "privacy_version": PRIVACY_VERSION,
                },
            }
        )
        user = getattr(auth_result, "user", None)
        user_id = str(getattr(user, "id", "") or "")
        if not user_id:
            raise RuntimeError("auth user was not created")
    except Exception as exc:
        logger.warning("signup auth creation failed email=%s: %s", email, exc)
        raise HTTPException(status_code=409, detail="This email may already be registered. Try signing in.") from exc

    plan_intent = payload.plan_intent if payload.plan_intent in {"starter", "pro"} else None
    try:
        supabase.table("workspaces").insert(
            {"slug": workspace_slug, "name": payload.workspace_name.strip(), "plan_id": "free", "owner_email": email}
        ).execute()
        supabase.table("workspace_users").insert(
            {"workspace_slug": workspace_slug, "user_id": user_id, "role": "owner"}
        ).execute()
        supabase.table("workspace_branding").insert(
            {"workspace_slug": workspace_slug, "workspace_name": payload.workspace_name.strip(), "enabled_workflows": ["release_intake"]}
        ).execute()
        link = _magic_link(
            request,
            user_id=user_id,
            workspace_slug=workspace_slug,
            purpose="signup",
        )
        send_workspace_magic_link_email(
            to_email=email,
            name=payload.name.strip(),
            workspace_name=payload.workspace_name.strip(),
            magic_link=link,
            locale=_locale(request),
            purpose="signup",
        )
    except Exception as exc:
        logger.exception("signup provisioning failed workspace=%s", workspace_slug)
        _rollback(user_id, workspace_slug)
        raise HTTPException(status_code=503, detail="The workspace could not be created. Try again.") from exc

    return {
        "ok": True,
        "workspace_slug": workspace_slug,
        "plan_intent": plan_intent,
        "requires_email_confirmation": True,
    }


@router.post("/magic-link")
def request_magic_link(payload: LoginRequest, request: Request):
    if payload.company_website:
        return {"ok": True}
    email = str(payload.email).strip().lower()
    _rate_limit(request, "login", subject=email)
    workspace = _first_row(
        supabase.table("workspaces").select("slug,name,owner_email").eq("owner_email", email).limit(1).execute()
    )
    if not workspace:
        return {"ok": True}
    membership = _first_row(
        supabase.table("workspace_users")
        .select("user_id,role")
        .eq("workspace_slug", str(workspace["slug"]))
        .eq("role", "owner")
        .limit(1)
        .execute()
    )
    if not membership:
        return {"ok": True}
    try:
        auth_result = supabase.auth.admin.get_user_by_id(str(membership["user_id"]))
        auth_user = getattr(auth_result, "user", None)
        auth_email = str(getattr(auth_user, "email", "") or "").strip().lower()
    except Exception:
        logger.exception("login user lookup failed workspace=%s", workspace["slug"])
        raise HTTPException(status_code=503, detail="The access email could not be sent. Try again.")
    if not auth_email or auth_email != email:
        logger.warning("login owner identity mismatch workspace=%s", workspace["slug"])
        return {"ok": True}
    try:
        link = _magic_link(
            request,
            user_id=str(membership["user_id"]),
            workspace_slug=str(workspace["slug"]),
            purpose="login",
        )
    except Exception as exc:
        logger.exception("login magic-link persistence failed workspace=%s", workspace["slug"])
        raise HTTPException(status_code=503, detail="The access email could not be sent. Try again.") from exc
    try:
        send_workspace_magic_link_email(
            to_email=email,
            name=email.split("@", 1)[0],
            workspace_name=str(workspace.get("name") or workspace["slug"]),
            magic_link=link,
            locale=_locale(request),
            purpose="login",
        )
    except Exception as exc:
        logger.exception("login magic-link delivery failed workspace=%s", workspace["slug"])
        raise HTTPException(status_code=503, detail="The access email could not be sent. Try again.") from exc
    return {"ok": True}


@router.get("/callback")
def magic_link_callback(token: str, request: Request):
    value = _verify_magic_token(token)
    if not value:
        return RedirectResponse(f"{_origin(request)}/login?error=invalid_link", status_code=303)
    user_id = str(value["uid"])
    workspace_slug = str(value["ws"])
    membership = _first_row(
        supabase.table("workspace_users")
        .select("user_id,workspace_slug")
        .eq("workspace_slug", workspace_slug)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not membership:
        return RedirectResponse(f"{_origin(request)}/login?error=workspace_access", status_code=303)
    if not _consume_magic_link(token, value):
        return RedirectResponse(f"{_origin(request)}/login?error=used_link", status_code=303)
    try:
        supabase.auth.admin.update_user_by_id(user_id, {"email_confirm": True})
    except Exception:
        logger.exception("magic-link confirmation failed user=%s", user_id)
        return RedirectResponse(f"{_origin(request)}/login?error=confirmation", status_code=303)
    try:
        portal_token = _issue_persistent_portal_session(
            user_id=user_id,
            workspace_slug=workspace_slug,
        )
    except Exception:
        logger.exception("portal session creation failed workspace=%s user=%s", workspace_slug, user_id)
        return RedirectResponse(f"{_origin(request)}/login?error=session", status_code=303)
    return RedirectResponse(
        f"{_origin(request)}/portal/{workspace_slug}#portal_token={portal_token}",
        status_code=303,
    )


@router.post("/logout")
def logout(x_portal_token: Optional[str] = Header(default=None)):
    token = (x_portal_token or "").strip()
    data = _decode_portal_token(token, allow_expired=True)
    if not data:
        raise HTTPException(status_code=401, detail="unauthorized")
    session_id = str(data.get("sid") or "")
    if session_id:
        try:
            (
                supabase.table("portal_sessions")
                .update({"revoked_at": datetime.now(timezone.utc).isoformat()})
                .eq("session_id", session_id)
                .eq("user_id", str(data.get("uid") or ""))
                .eq("workspace_slug", str(data.get("ws") or ""))
                .execute()
            )
        except Exception as exc:
            logger.exception("portal logout failed session=%s", session_id)
            raise HTTPException(status_code=503, detail="Session revocation is unavailable.") from exc
    return {"ok": True}
