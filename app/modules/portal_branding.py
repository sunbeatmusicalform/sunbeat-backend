"""
portal_branding.py
Edição self-service da marca do tenant ("Minha marca").

  PATCH /workspaces/{workspace_slug}/branding
    Header: X-Portal-Token (sessão do portal) ou X-Admin-Token (admin)
    Body: campos permitidos da tabela workspace_branding; omitidos não são gravados.

logo_url aceita URL pública ou data URL (upload convertido no front, v1;
migração para Supabase Storage na fase 2).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.admin_auth import _admin_token_is_valid
from app.core.database import supabase
from app.modules.portal_session import require_portal_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["portal-branding"])

ALLOWED_TEXT_FIELDS = {
    "workspace_name",
    "slogan",
    "form_title",
    "intro_text",
    "success_message",
    "logo_url",
    "banner_url",
    "badge_url",
    "social_image_url",
    "social_title",
    "social_description",
    "form_bg_color",
    "primary_color",
}

_COLOR_FIELDS = {"form_bg_color", "primary_color"}
_MAX_TEXT_LEN = 4000
_MAX_LOGO_DATA_URL_LEN = 400_000  # ~300 KB em base64


class BrandingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_name: Optional[str] = Field(default=None, max_length=200)
    slogan: Optional[str] = Field(default=None, max_length=300)
    form_title: Optional[str] = Field(default=None, max_length=200)
    intro_text: Optional[str] = Field(default=None, max_length=_MAX_TEXT_LEN)
    success_message: Optional[str] = Field(default=None, max_length=_MAX_TEXT_LEN)
    logo_url: Optional[str] = None
    banner_url: Optional[str] = None
    badge_url: Optional[str] = None
    social_image_url: Optional[str] = None
    social_title: Optional[str] = Field(default=None, max_length=300)
    social_description: Optional[str] = Field(default=None, max_length=600)
    form_bg_color: Optional[str] = Field(default=None, max_length=20)
    primary_color: Optional[str] = Field(default=None, max_length=20)


def _validate_fields(fields: dict) -> dict:
    clean: dict = {}
    for key, value in fields.items():
        if key not in ALLOWED_TEXT_FIELDS or value is None:
            continue
        value = str(value).strip()
        if key in _COLOR_FIELDS and value and not value.startswith("#"):
            raise HTTPException(status_code=422, detail=f"{key} deve ser cor hexadecimal (#rrggbb)")
        if key == "logo_url" and value.startswith("data:") and len(value) > _MAX_LOGO_DATA_URL_LEN:
            raise HTTPException(status_code=422, detail="logo muito grande — use imagem até ~300 KB")
        clean[key] = value or None
    return clean


async def _require_portal_or_admin(
    workspace_slug: str,
    x_portal_token: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> None:
    if x_admin_token and _admin_token_is_valid(x_admin_token.strip()):
        return
    require_portal_session(workspace_slug, x_portal_token)


@router.patch("/{workspace_slug}/branding")
async def patch_branding(
    workspace_slug: str,
    body: BrandingPatch,
    _: None = Depends(_require_portal_or_admin),
):
    fields = _validate_fields(body.model_dump(exclude_unset=True))
    if not fields:
        return {"ok": True, "workspace_slug": workspace_slug, "updated": []}

    from datetime import datetime, timezone

    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        supabase.table("workspace_branding").update(fields).eq(
            "workspace_slug", workspace_slug
        ).execute()
    except Exception as exc:
        logger.exception("branding update failed")
        raise HTTPException(status_code=500, detail=f"failed to update branding: {exc}")

    updated = [k for k in fields if k != "updated_at"]
    logger.info("branding updated for %s: %s", workspace_slug, updated)
    return {"ok": True, "workspace_slug": workspace_slug, "updated": updated}
