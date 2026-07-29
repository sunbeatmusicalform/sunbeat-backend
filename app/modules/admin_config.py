"""
admin_config.py
API interna de leitura e edição de configuração por workspace + workflow.

Protegida por header X-Admin-Token (shared secret, variável INTERNAL_ADMIN_TOKEN).
Retorna 401 — nunca 422 — quando o header está ausente ou incorreto.

Sem UI. Consumível por Postman, curl ou futura Setup AI.

Rotas:
  GET   /internal/config/{workspace_slug}/{workflow_type}
  GET   /internal/config/{workspace_slug}/{workflow_type}/airtable
  PATCH /internal/config/{workspace_slug}/{workflow_type}/flags
  PATCH /internal/config/{workspace_slug}/{workflow_type}/email
  PATCH /internal/config/{workspace_slug}/{workflow_type}/airtable
  POST  /internal/config/setup-ai/airtable
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings
from app.core.database import supabase
from app.core.admin_auth import require_admin_token
from app.services.workspace_config import get_workflow_operational_base

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

_AIRTABLE_TABLE_OVERRIDE_KEYS: Dict[str, str] = {
    "company_registry": "company_registry_table_override",
    "people_registry": "people_registry_table_override",
}

_AIRTABLE_CONTRACT_KEYS = [
    "base_id_override",
    "merge_keys",
    "field_map",
]

_SETUP_AI_AIRTABLE_CONTRACT_VERSION = "airtable_extra_settings.v1"

_SETUP_AI_AIRTABLE_WORKFLOWS = {
    "company_registry",
    "people_registry",
}

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

_require_admin_token = require_admin_token


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


class AirtableMergeKeyPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str = Field(..., min_length=1)
    airtable_field: str = Field(..., min_length=1)
    normalization: Optional[str] = None
    priority: Optional[int] = None


class AirtableExtraPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_id_override: Optional[str] = None
    table_override: Optional[str] = None
    company_registry_table_override: Optional[str] = None
    people_registry_table_override: Optional[str] = None
    people_registry_legacy_table_override: Optional[str] = None
    merge_keys: Optional[List[AirtableMergeKeyPatch]] = None
    field_map: Optional[Dict[str, Any]] = None

    @field_validator(
        "base_id_override",
        "table_override",
        "company_registry_table_override",
        "people_registry_table_override",
        "people_registry_legacy_table_override",
        mode="before",
    )
    @classmethod
    def blank_strings_to_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class AirtablePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    airtable_sync_enabled: Optional[bool] = None
    airtable: Optional[AirtableExtraPatch] = None


class SetupAIAirtableConfigAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["read", "patch", "preview_patch", "apply_patch"]
    workspace_slug: str = Field(..., min_length=1)
    workflow_type: str = Field(..., min_length=1)
    confirm_apply: Optional[bool] = None
    airtable_sync_enabled: Optional[bool] = None
    airtable: Optional[AirtableExtraPatch] = None

    @field_validator("workspace_slug", "workflow_type", mode="before")
    @classmethod
    def strip_required_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_operation_payload(self) -> "SetupAIAirtableConfigAction":
        has_flag = "airtable_sync_enabled" in self.model_fields_set
        has_airtable_field = "airtable" in self.model_fields_set
        has_airtable_patch = (
            self.airtable is not None and bool(self.airtable.model_fields_set)
        )

        if self.operation == "read" and (has_flag or has_airtable_field):
            raise ValueError("read operation does not accept patch fields")

        if self.operation in {"patch", "preview_patch", "apply_patch"} and not (
            has_flag or has_airtable_patch
        ):
            raise ValueError(
                f"{self.operation} operation requires airtable_sync_enabled "
                "or airtable fields"
            )

        if self.operation == "apply_patch" and self.confirm_apply is not True:
            raise ValueError("apply_patch operation requires confirm_apply=true")

        if self.operation != "apply_patch" and "confirm_apply" in self.model_fields_set:
            raise ValueError("confirm_apply is only accepted for apply_patch")

        return self


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


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _airtable_table_override_key(workflow_type: str) -> Optional[str]:
    return _AIRTABLE_TABLE_OVERRIDE_KEYS.get(workflow_type)


def _airtable_defaults(workflow_type: str) -> Dict[str, Any]:
    return get_workflow_operational_base(workflow_type).get("airtable") or {}


def _raw_airtable_extra(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    extra = row.get("extra_settings") if row else None
    if not isinstance(extra, dict):
        return {}
    airtable = extra.get("airtable") or {}
    return airtable if isinstance(airtable, dict) else {}


def _resolve_airtable_with_origin(
    row: Optional[Dict[str, Any]],
    workflow_type: str,
) -> Dict[str, Any]:
    defaults = _airtable_defaults(workflow_type)
    raw = _raw_airtable_extra(row)
    effective = _deep_merge_dict(defaults, raw)
    table_key = _airtable_table_override_key(workflow_type)

    keys = set(defaults.keys()) | set(raw.keys()) | set(_AIRTABLE_CONTRACT_KEYS)
    if table_key:
        keys.add(table_key)

    origins = {
        key: "db" if key in raw else "default" if key in defaults else "missing"
        for key in sorted(keys)
    }

    return {
        "effective": effective,
        "raw": raw,
        "origins": origins,
        "contract": {
            "table_override_key": table_key,
            "accepted_table_override_aliases": (
                ["table_override", table_key] if table_key else []
            ),
            "field_map_runtime": "service_owned",
            "merge_keys_runtime": "service_owned",
        },
    }


def _normalize_airtable_patch(
    workflow_type: str,
    patch: AirtableExtraPatch,
) -> Dict[str, Any]:
    fields = patch.model_dump(exclude_unset=True, mode="json")
    table_key = _airtable_table_override_key(workflow_type)

    if "table_override" in fields:
        table_override = fields.pop("table_override")
        if not table_key:
            raise HTTPException(
                status_code=422,
                detail="table_override is not supported for this workflow_type",
            )
        if table_key in fields and fields[table_key] != table_override:
            raise HTTPException(
                status_code=422,
                detail=(
                    "table_override conflicts with "
                    f"{table_key} for workflow_type={workflow_type}"
                ),
            )
        fields[table_key] = table_override

    for candidate_key in _AIRTABLE_TABLE_OVERRIDE_KEYS.values():
        if candidate_key in fields and candidate_key != table_key:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{candidate_key} is not valid for workflow_type={workflow_type}"
                ),
            )

    return fields


def _deep_merge_airtable(
    current_extra: Dict[str, Any],
    workflow_type: str,
    patch: AirtableExtraPatch,
) -> tuple[Dict[str, Any], List[str]]:
    normalized = _normalize_airtable_patch(workflow_type, patch)
    result = copy.deepcopy(current_extra)
    current_airtable = result.get("airtable") or {}
    if not isinstance(current_airtable, dict):
        current_airtable = {}
    result["airtable"] = _deep_merge_dict(current_airtable, normalized)
    return result, sorted(normalized.keys())


def _apply_airtable_patch(
    workspace_slug: str,
    workflow_type: str,
    body: AirtablePatch,
) -> Dict[str, Any]:
    """
    Aplica o mesmo patch parcial usado pela rota PATCH interna.

    Mantem a escrita limitada as colunas top-level conhecidas e ao bloco
    `extra_settings.airtable`, sem tocar em sync services ou submit publico.
    """
    has_flag = "airtable_sync_enabled" in body.model_fields_set
    has_airtable_patch = (
        body.airtable is not None and bool(body.airtable.model_fields_set)
    )

    if not has_flag and not has_airtable_patch:
        return {
            "ok": True,
            "updated": [],
            "airtable_updated": [],
            "table_override_key": _airtable_table_override_key(workflow_type),
        }

    payload: Dict[str, Any] = {
        "workspace_slug": workspace_slug,
        "workflow_type": workflow_type,
    }
    updated: List[str] = []
    airtable_updated: List[str] = []

    if has_flag:
        payload["airtable_sync_enabled"] = body.airtable_sync_enabled
        updated.append("airtable_sync_enabled")

    if has_airtable_patch and body.airtable is not None:
        row = _read_raw_row(workspace_slug, workflow_type)
        current_extra: Dict[str, Any] = (
            row.get("extra_settings") or {}
        ) if row else {}
        if not isinstance(current_extra, dict):
            current_extra = {}

        merged, airtable_updated = _deep_merge_airtable(
            current_extra,
            workflow_type,
            body.airtable,
        )
        payload["extra_settings"] = merged
        updated.append("extra_settings.airtable")

    try:
        supabase.table("workspace_workflow_settings").upsert(
            payload,
            on_conflict="workspace_slug,workflow_type",
        ).execute()
    except Exception as exc:
        logger.error(
            "admin_config: erro ao gravar airtable workspace=%s workflow=%s: %s",
            workspace_slug, workflow_type, exc,
        )
        raise HTTPException(status_code=500, detail=f"Erro ao gravar airtable: {exc}")

    return {
        "ok": True,
        "updated": updated,
        "airtable_updated": airtable_updated,
        "table_override_key": _airtable_table_override_key(workflow_type),
    }


def _validate_setup_ai_airtable_workflow(workflow_type: str) -> None:
    if workflow_type not in _SETUP_AI_AIRTABLE_WORKFLOWS:
        raise HTTPException(
            status_code=422,
            detail=(
                "workflow_type is not supported by the setup AI Airtable "
                "config consumer"
            ),
        )


def _setup_ai_airtable_warnings(body: AirtablePatch) -> List[str]:
    if body.airtable is None:
        return []

    warnings: List[str] = []
    fields = body.airtable.model_fields_set
    if "merge_keys" in fields:
        warnings.append(
            "merge_keys is metadata only in this phase; sync services keep "
            "their runtime merge policy."
        )
    if "field_map" in fields:
        warnings.append(
            "field_map is metadata only in this phase; sync services keep "
            "their runtime payload builders."
        )
    return warnings


def _setup_ai_applied_patch(
    workflow_type: str,
    body: AirtablePatch,
) -> Dict[str, Any]:
    applied: Dict[str, Any] = {}
    if "airtable_sync_enabled" in body.model_fields_set:
        applied["airtable_sync_enabled"] = body.airtable_sync_enabled

    if body.airtable is not None and body.airtable.model_fields_set:
        applied["airtable"] = _normalize_airtable_patch(workflow_type, body.airtable)

    return applied


def _setup_ai_airtable_response(
    workspace_slug: str,
    workflow_type: str,
    operation: Literal["read", "patch", "preview_patch", "apply_patch"],
    row: Optional[Dict[str, Any]],
    applied_patch: Optional[Dict[str, Any]] = None,
    warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    flags = _resolve_flags_with_origin(row, workflow_type)
    airtable = _resolve_airtable_with_origin(row, workflow_type)

    return {
        "ok": True,
        "operation": operation,
        "workspace_slug": workspace_slug,
        "workflow_type": workflow_type,
        "contract_version": _SETUP_AI_AIRTABLE_CONTRACT_VERSION,
        "source": "workspace_workflow_settings.extra_settings.airtable",
        "airtable_sync_enabled": flags["airtable_sync_enabled"],
        "effective": airtable["effective"],
        "raw": airtable["raw"],
        "origins": airtable["origins"],
        "contract": airtable["contract"],
        "applied_patch": applied_patch or {},
        "warnings": warnings or [],
    }


def _setup_ai_patch_body_from_action(
    body: SetupAIAirtableConfigAction,
) -> AirtablePatch:
    patch_payload: Dict[str, Any] = {}
    if "airtable_sync_enabled" in body.model_fields_set:
        patch_payload["airtable_sync_enabled"] = body.airtable_sync_enabled
    if body.airtable is not None:
        patch_payload["airtable"] = body.airtable.model_dump(
            exclude_unset=True,
            mode="json",
        )
    return AirtablePatch.model_validate(patch_payload)


def _preview_airtable_patch(
    workspace_slug: str,
    workflow_type: str,
    body: AirtablePatch,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], List[str], List[str]]:
    """
    Projeta o resultado de um patch sem persistir.

    Usado pela primeira superficie controlada da Setup AI para permitir revisao
    humana antes de gravar em `workspace_workflow_settings`.
    """
    row = _read_raw_row(workspace_slug, workflow_type)
    preview_row = copy.deepcopy(row) if row is not None else {}
    updated: List[str] = []
    airtable_updated: List[str] = []

    if "airtable_sync_enabled" in body.model_fields_set:
        preview_row["airtable_sync_enabled"] = body.airtable_sync_enabled
        updated.append("airtable_sync_enabled")

    if body.airtable is not None and body.airtable.model_fields_set:
        current_extra: Dict[str, Any] = (
            row.get("extra_settings") or {}
        ) if row else {}
        if not isinstance(current_extra, dict):
            current_extra = {}
        merged, airtable_updated = _deep_merge_airtable(
            current_extra,
            workflow_type,
            body.airtable,
        )
        preview_row["extra_settings"] = merged
        updated.append("extra_settings.airtable")

    return row, preview_row, updated, airtable_updated


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
        "airtable":       _resolve_airtable_with_origin(row, workflow_type),
        "env_snapshot":   _env_snapshot(),
    }


@router.get("/{workspace_slug}/{workflow_type}/airtable")
async def get_airtable_config(
    workspace_slug: str,
    workflow_type: str,
    _: None = Depends(_require_admin_token),
) -> Dict[str, Any]:
    """Retorna contrato efetivo de extra_settings.airtable para leitura assistida."""
    row = _read_raw_row(workspace_slug, workflow_type)
    return {
        "workspace_slug": workspace_slug,
        "workflow_type": workflow_type,
        "airtable_sync_enabled": _resolve_flags_with_origin(
            row,
            workflow_type,
        )["airtable_sync_enabled"],
        "airtable": _resolve_airtable_with_origin(row, workflow_type),
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


@router.patch("/{workspace_slug}/{workflow_type}/airtable")
async def patch_airtable(
    workspace_slug: str,
    workflow_type: str,
    body: AirtablePatch,
    _: None = Depends(_require_admin_token),
) -> Dict[str, Any]:
    """
    Atualiza parcialmente a configuracao Airtable do workflow.

    Escreve flags efetivas nas colunas top-level e faz deep merge apenas em
    `extra_settings.airtable`, preservando `email`, `drive`, `operational_base`
    e quaisquer outros blocos existentes.
    """
    return _apply_airtable_patch(workspace_slug, workflow_type, body)


@router.post("/setup-ai/airtable")
async def setup_ai_airtable_config_action(
    body: SetupAIAirtableConfigAction,
    _: None = Depends(_require_admin_token),
) -> Dict[str, Any]:
    """
    Superficie minima e estruturada para futura Setup AI.

    Aceita apenas operacoes estruturadas. `preview_patch` permite revisar o
    resultado consolidado antes de `apply_patch`, que exige confirmacao
    explicita. Nao interpreta linguagem natural, nao orquestra sync e nao altera
    runtime de `merge_keys` ou `field_map`.
    """
    _validate_setup_ai_airtable_workflow(body.workflow_type)

    if body.operation == "read":
        row = _read_raw_row(body.workspace_slug, body.workflow_type)
        return _setup_ai_airtable_response(
            body.workspace_slug,
            body.workflow_type,
            "read",
            row,
        )

    patch_body = _setup_ai_patch_body_from_action(body)
    applied_patch = _setup_ai_applied_patch(body.workflow_type, patch_body)
    warnings = _setup_ai_airtable_warnings(patch_body)

    if body.operation == "preview_patch":
        current_row, preview_row, updated, airtable_updated = _preview_airtable_patch(
            body.workspace_slug,
            body.workflow_type,
            patch_body,
        )
        response = _setup_ai_airtable_response(
            body.workspace_slug,
            body.workflow_type,
            "preview_patch",
            preview_row,
            applied_patch=applied_patch,
            warnings=warnings,
        )
        response.update(
            {
                "dry_run": True,
                "requires_confirmation": True,
                "updated": updated,
                "airtable_updated": airtable_updated,
                "current": _setup_ai_airtable_response(
                    body.workspace_slug,
                    body.workflow_type,
                    "read",
                    current_row,
                ),
            }
        )
        return response

    if body.operation == "apply_patch":
        current_row = _read_raw_row(body.workspace_slug, body.workflow_type)
        apply_result = _apply_airtable_patch(
            body.workspace_slug,
            body.workflow_type,
            patch_body,
        )
        row = _read_raw_row(body.workspace_slug, body.workflow_type)
        response = _setup_ai_airtable_response(
            body.workspace_slug,
            body.workflow_type,
            "apply_patch",
            row,
            applied_patch=applied_patch,
            warnings=warnings,
        )
        response.update(
            {
                "dry_run": False,
                "confirmed": True,
                "updated": apply_result["updated"],
                "airtable_updated": apply_result["airtable_updated"],
                "current_before": _setup_ai_airtable_response(
                    body.workspace_slug,
                    body.workflow_type,
                    "read",
                    current_row,
                ),
            }
        )
        return response

    _apply_airtable_patch(body.workspace_slug, body.workflow_type, patch_body)

    row = _read_raw_row(body.workspace_slug, body.workflow_type)
    return _setup_ai_airtable_response(
        body.workspace_slug,
        body.workflow_type,
        "patch",
        row,
        applied_patch=applied_patch,
        warnings=warnings,
    )
