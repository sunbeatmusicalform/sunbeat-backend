"""
admin_config.py
API interna de leitura e edição de configuração por workspace + workflow.

Protegida por header X-Admin-Token (shared secret, variável INTERNAL_ADMIN_TOKEN).
Retorna 401 — nunca 422 — quando o header está ausente ou incorreto.

Sem UI. Consumível por Postman, curl ou futura Setup AI.

Rotas:
  GET   /internal/config/{workspace_slug}/{workflow_type}
  PATCH /internal/config/{workspace_slug}/{workflow_type}/flags
  PATCH /internal/config/{workspace_slug}/{workflow_type}/email
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.core.database import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/config", tags=["internal_config"])

# ---------------------------------------------------------------------------
# Flags operacionais da v1
# edit_mode_enabled excluído: definido em workspace_config.py mas sem consumo
# confirmado em submissions.py ou release_drafts.py nesta versão.
# ---------------------------------------------------------------------------
_V1_FLAGS = [
    "post_submit_email_enabled",
    "edit_email_enabled",
    "airtable_sync_enabled",
    "drive_sync_enabled",
]

_EMAIL_EVENTS = ["on_draft", "on_submit", "on_edit", "on_first_stage", "on_summary"]

_ENV_FIELDS = [
    "GOOGLE_DRIVE_ENABLED",
    "AIRTABLE_RIGHTS_CLEARANCE_MUSICAL_ENABLED",
    "AIRTABLE_COMPANY_REGISTRY_ENABLED",
    "AIRTABLE_PEOPLE_REGISTRY_SYNC_ENABLED",
]

# Defaults por workflow para os 4 flags da v1 (espelha _WORKFLOW_DEFAULTS de workspace_config.py)
_FLAG_DEFAULTS: Dict[str, Dict[str, bool]] = {
    "release_intake":   {"post_submit_email_enabled": True,  "edit_email_enabled": True,  "airtable_sync_enabled": True,  "drive_sync_enabled": True},
    "rights_clearance": {"post_submit_email_enabled": True,  "edit_email_enabled": True,  "airtable_sync_enabled": True,  "drive_sync_enabled": True},
    "company_registry": {"post_submit_email_enabled": True,  "edit_email_enabled": True,  "airtable_sync_enabled": True,  "drive_sync_enabled": False},
    "people_registry":  {"post_submit_email_enabled": False, "edit_email_enabled": False, "airtable_sync_enabled": True,  "drive_sync_enabled": False},
}


# ---------------------------------------------------------------------------
# Auth — Header opcional → 401 explícito, nunca 422
# ---------------------------------------------------------------------------

async def _require_admin_token(
    x_admin_token: Optional[str] = Header(default=None),
) -> None:
    expected = settings.INTERNAL_ADMIN_TOKEN
    if not expected or not x_admin_token or x_admin_token != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class FlagsPatch(BaseModel):
    post_submit_email_enabled: Optional[bool] = None
    edit_email_enabled:        Optional[bool] = None
    airtable_sync_enabled:     Optional[bool] = None
    drive_sync_enabled:        Optional[bool] = None


class EmailEventPatch(BaseModel):
    enabled:    bool
    recipients: List[str] = Field(default_factory=list)

    @field_validator("recipients")
    @classmethod
    def max_five_recipients(cls, v: List[str]) -> List[str]:
        if len(v) > 5:
            raise ValueError(
                f"recipients aceita no máximo 5 endereços, recebido {len(v)}"
            )
        return v


class EmailEventsPatch(BaseModel):
    on_draft:       Optional[EmailEventPatch] = None
    on_submit:      Optional[EmailEventPatch] = None
    on_edit:        Optional[EmailEventPatch] = None
    on_first_stage: Optional[EmailEventPatch] = None
    on_summary:     Optional[EmailEventPatch] = None


class EmailPatch(BaseModel):
    events:   Optional[EmailEventsPatch] = None
    variants: Optional[Dict[str, EmailEventsPatch]] = None


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _read_raw_row(workspace_slug: str, workflow_type: str) -> Optional[Dict[str, Any]]:
    """Lê a linha crua do Supabase. Retorna None se não existir ou em caso de erro."""
    try:
        result = (
            supabase.table("workspace_workflow_settings")
            .select(
                "post_submit_email_enabled, edit_email_enabled, "
                "airtable_sync_enabled, drive_sync_enabled, extra_settings"
            )
            .eq("workspace_slug", workspace_slug)
            .eq("workflow_type", workflow_type)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None
    except Exception as exc:
        logger.warning(
            "admin_config: erro ao ler row workspace=%s workflow=%s: %s",
            workspace_slug, workflow_type, exc,
        )
        return None


def _resolve_flags_with_origin(
    row: Optional[Dict[str, Any]],
    workflow_type: str,
) -> Dict[str, Any]:
    defaults = _FLAG_DEFAULTS.get(workflow_type, {})
    result: Dict[str, Any] = {}
    for flag in _V1_FLAGS:
        if row is not None and row.get(flag) is not None:
            result[flag] = {"value": row[flag], "_origin": "db"}
        else:
            result[flag] = {"value": defaults.get(flag, False), "_origin": "default"}
    return result


def _resolve_email_with_origin(extra_settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    email = (extra_settings or {}).get("email") or {}
    events_db: Dict[str, Any] = email.get("events") or {}
    variants_db: Dict[str, Any] = email.get("variants") or {}

    events_out: Dict[str, Any] = {}
    for ev in _EMAIL_EVENTS:
        if ev in events_db:
            cfg = events_db[ev] or {}
            events_out[ev] = {
                "enabled":    bool(cfg.get("enabled", True)),
                "recipients": list(cfg.get("recipients") or [])[:5],
                "_origin":    "db",
            }

    variants_out: Dict[str, Any] = {}
    for vname, vdata in variants_db.items():
        variant_events: Dict[str, Any] = {}
        for ev in _EMAIL_EVENTS:
            if ev in (vdata or {}):
                cfg = vdata[ev] or {}
                variant_events[ev] = {
                    "enabled":    bool(cfg.get("enabled", True)),
                    "recipients": list(cfg.get("recipients") or [])[:5],
                    "_origin":    "db",
                }
        if variant_events:
            variants_out[vname] = variant_events

    return {"events": events_out, "variants": variants_out}


def _env_snapshot() -> Dict[str, Any]:
    return {
        field: {"value": getattr(settings, field, None), "_origin": "env"}
        for field in _ENV_FIELDS
    }


def _deep_merge_email(
    current_extra: Dict[str, Any],
    patch: EmailPatch,
) -> Dict[str, Any]:
    """
    Merge parcial seguro: toca apenas nos blocos explicitamente enviados no body.
    Campos omitidos no patch permanecem intocados no Supabase.
    """
    result = copy.deepcopy(current_extra)
    email = result.setdefault("email", {})

    if patch.events is not None:
        events_block = email.setdefault("events", {})
        for ev_name in _EMAIL_EVENTS:
            ev_patch = getattr(patch.events, ev_name, None)
            if ev_patch is not None:
                events_block[ev_name] = {
                    "enabled":    ev_patch.enabled,
                    "recipients": ev_patch.recipients,
                }

    if patch.variants is not None:
        variants_block = email.setdefault("variants", {})
        for vname, vdata in patch.variants.items():
            variant_block = variants_block.setdefault(vname, {})
            for ev_name in _EMAIL_EVENTS:
                ev_patch = getattr(vdata, ev_name, None)
                if ev_patch is not None:
                    variant_block[ev_name] = {
                        "enabled":    ev_patch.enabled,
                        "recipients": ev_patch.recipients,
                    }

    return result


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@router.get("/{workspace_slug}/{workflow_type}")
async def get_config(
    workspace_slug: str,
    workflow_type: str,
    _: None = Depends(_require_admin_token),
) -> Dict[str, Any]:
    """Retorna config efetiva com _origin por campo (db / default / env)."""
    row = _read_raw_row(workspace_slug, workflow_type)
    extra = row.get("extra_settings") if row else None
    return {
        "workspace_slug": workspace_slug,
        "workflow_type":  workflow_type,
        "flags":          _resolve_flags_with_origin(row, workflow_type),
        "email":          _resolve_email_with_origin(extra),
        "env_snapshot":   _env_snapshot(),
    }


@router.patch("/{workspace_slug}/{workflow_type}/flags")
async def patch_flags(
    workspace_slug: str,
    workflow_type: str,
    body: FlagsPatch,
    _: None = Depends(_require_admin_token),
) -> Dict[str, Any]:
    """
    Atualiza apenas os flags enviados no body.
    Campos omitidos não são gravados — não persiste defaults como configuração explícita.
    """
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return {"ok": True, "updated": []}

    payload = {
        "workspace_slug": workspace_slug,
        "workflow_type":  workflow_type,
        **fields,
    }
    try:
        supabase.table("workspace_workflow_settings").upsert(
            payload,
            on_conflict="workspace_slug,workflow_type",
        ).execute()
    except Exception as exc:
        logger.error(
            "admin_config: erro ao gravar flags workspace=%s workflow=%s: %s",
            workspace_slug, workflow_type, exc,
        )
        raise HTTPException(status_code=500, detail=f"Erro ao gravar flags: {exc}")

    return {"ok": True, "updated": list(fields.keys())}


@router.patch("/{workspace_slug}/{workflow_type}/email")
async def patch_email(
    workspace_slug: str,
    workflow_type: str,
    body: EmailPatch,
    _: None = Depends(_require_admin_token),
) -> Dict[str, Any]:
    """
    Atualiza apenas os eventos/variantes de email enviados no body.
    Usa merge parcial: blocos omitidos permanecem intocados no Supabase.
    """
    has_events = body.events is not None and any(
        getattr(body.events, ev) is not None for ev in _EMAIL_EVENTS
    )
    has_variants = bool(body.variants)
    if not has_events and not has_variants:
        return {"ok": True, "updated_events": [], "updated_variants": []}

    row = _read_raw_row(workspace_slug, workflow_type)
    current_extra: Dict[str, Any] = (row.get("extra_settings") or {}) if row else {}
    merged = _deep_merge_email(current_extra, body)

    try:
        supabase.table("workspace_workflow_settings").upsert(
            {
                "workspace_slug": workspace_slug,
                "workflow_type":  workflow_type,
                "extra_settings": merged,
            },
            on_conflict="workspace_slug,workflow_type",
        ).execute()
    except Exception as exc:
        logger.error(
            "admin_config: erro ao gravar email workspace=%s workflow=%s: %s",
            workspace_slug, workflow_type, exc,
        )
        raise HTTPException(status_code=500, detail=f"Erro ao gravar email: {exc}")

    updated_events: List[str] = []
    if body.events is not None:
        updated_events = [ev for ev in _EMAIL_EVENTS if getattr(body.events, ev) is not None]

    updated_variants: List[str] = []
    if body.variants:
        for vname, vdata in body.variants.items():
            for ev in _EMAIL_EVENTS:
                if getattr(vdata, ev) is not None:
                    updated_variants.append(f"{vname}.{ev}")

    return {"ok": True, "updated_events": updated_events, "updated_variants": updated_variants}
