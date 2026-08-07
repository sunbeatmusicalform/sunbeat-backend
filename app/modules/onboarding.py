"""Onboarding governado do workspace, conduzido pelo MotoSchema.

O portal pode ler o estado, gerar uma previa assinada e somente depois aplicar
a configuracao. A aplicacao exige uma sessao valida do workspace, uma previa
nao expirada e a disponibilidade da trilha de auditoria.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.database import supabase
from app.modules.portal_session import require_portal_session
from app.modules.workflow_registry import list_registered_workflows
from app.services.self_service_entitlements import (
    WorkspaceEntitlements,
    is_self_service_workspace,
    load_workspace_entitlements,
)

logger = logging.getLogger("sunbeat.onboarding")

router = APIRouter(prefix="/workspaces", tags=["onboarding"])

ONBOARDING_WORKFLOW_TYPE = "__workspace_onboarding__"
PREVIEW_TTL_SECONDS = 30 * 60
FREE_ASSET_RETENTION_DAYS = 60

OPERATION_TYPES = {
    "label",
    "artist_management",
    "publisher",
    "agency",
    "distributor",
    "independent_artist",
    "other",
}
TEAM_SIZES = {"1", "2-5", "6-15", "16+"}
MONTHLY_VOLUMES = {"1-10", "11-50", "51-200", "200+"}
INTEGRATIONS = {"airtable", "google_drive", "email", "slack", "webhooks"}


class OnboardingProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    operation_type: str = Field(default="label", alias="operationType")
    team_size: str = Field(default="1", alias="teamSize")
    monthly_volume: str = Field(default="1-10", alias="monthlyVolume")
    workflow_types: list[str] = Field(default_factory=list, alias="workflowTypes")
    integrations: list[str] = Field(default_factory=list)
    primary_goal: str = Field(default="", alias="primaryGoal", max_length=600)


class OnboardingAction(BaseModel):
    operation: Literal["preview_patch", "apply_patch"]
    profile: OnboardingProfile
    preview_token: Optional[str] = None


def _first_row(result: Any) -> Optional[Dict[str, Any]]:
    rows = getattr(result, "data", None) or []
    return rows[0] if rows else None


def _active_workflows() -> list[str]:
    return [
        str(item["workflow_type"])
        for item in list_registered_workflows()
        if item.get("status") == "active"
    ]


def _allowed_workflows(entitlements: WorkspaceEntitlements) -> list[str]:
    active = _active_workflows()
    configured = entitlements.enabled_workflow_types
    return active if configured is None else [item for item in active if item in configured]


def _unique_allowed(values: Any, allowed: set[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item in allowed and item not in result:
            result.append(item)
    return result


def _normalize_profile(
    profile: OnboardingProfile | Dict[str, Any] | None,
    *,
    allowed_workflows: list[str],
    fallback_workflows: Optional[list[str]] = None,
) -> Dict[str, Any]:
    if isinstance(profile, OnboardingProfile):
        source = profile.model_dump(by_alias=True)
    elif isinstance(profile, dict):
        source = profile
    else:
        source = {}

    allowed = allowed_workflows
    allowed_set = set(allowed)
    requested = _unique_allowed(source.get("workflowTypes"), allowed_set)
    fallback = _unique_allowed(fallback_workflows or [], allowed_set)
    workflows = requested or fallback or allowed[:1]
    if "release_intake" in allowed_set and "release_intake" not in workflows:
        workflows.insert(0, "release_intake")

    operation_type = str(source.get("operationType") or "label")
    team_size = str(source.get("teamSize") or "1")
    monthly_volume = str(source.get("monthlyVolume") or "1-10")

    return {
        "operationType": operation_type if operation_type in OPERATION_TYPES else "label",
        "teamSize": team_size if team_size in TEAM_SIZES else "1",
        "monthlyVolume": monthly_volume if monthly_volume in MONTHLY_VOLUMES else "1-10",
        "workflowTypes": workflows,
        "integrations": _unique_allowed(source.get("integrations"), INTEGRATIONS),
        "primaryGoal": str(source.get("primaryGoal") or "").strip()[:600],
    }


def _preview_key() -> str:
    from app.core.config import settings

    key = (settings.INTERNAL_ADMIN_TOKEN or settings.AI_COPILOT_SECRET or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="onboarding preview secret is not configured")
    return key


def _profile_digest(workspace_slug: str, profile: Dict[str, Any]) -> str:
    payload = json.dumps(
        {"workspace_slug": workspace_slug, "profile": profile},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _issue_preview_token(workspace_slug: str, profile: Dict[str, Any]) -> tuple[str, str]:
    expires_at = int(time.time()) + PREVIEW_TTL_SECONDS
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "ws": workspace_slug,
                "digest": _profile_digest(workspace_slug, profile),
                "exp": expires_at,
            },
            separators=(",", ":"),
        ).encode()
    ).decode().rstrip("=")
    signature = hmac.new(_preview_key().encode(), payload.encode(), hashlib.sha256).hexdigest()
    expires_iso = datetime.fromtimestamp(expires_at, timezone.utc).isoformat()
    return f"{payload}.{signature}", expires_iso


def _valid_preview_token(token: str, workspace_slug: str, profile: Dict[str, Any]) -> bool:
    payload, separator, signature = (token or "").rpartition(".")
    if not separator:
        return False
    expected = hmac.new(_preview_key().encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        padded = payload + "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return False
    return bool(
        decoded.get("ws") == workspace_slug
        and decoded.get("digest") == _profile_digest(workspace_slug, profile)
        and int(decoded.get("exp") or 0) > int(time.time())
    )


def _load_workspace(workspace_slug: str) -> Dict[str, Any]:
    try:
        row = _first_row(
            supabase.table("workspaces")
            .select("slug,name,plan_id")
            .eq("slug", workspace_slug)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.exception("onboarding workspace lookup failed workspace=%s", workspace_slug)
        raise HTTPException(status_code=503, detail="workspace configuration is unavailable") from exc
    if not row:
        raise HTTPException(status_code=404, detail="workspace not found")
    return row


def _load_settings(workspace_slug: str) -> Optional[Dict[str, Any]]:
    try:
        return _first_row(
            supabase.table("workspace_workflow_settings")
            .select("extra_settings")
            .eq("workspace_slug", workspace_slug)
            .eq("workflow_type", ONBOARDING_WORKFLOW_TYPE)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.exception("onboarding settings lookup failed workspace=%s", workspace_slug)
        raise HTTPException(status_code=503, detail="onboarding configuration is unavailable") from exc


def _load_provisioned_workflows(workspace_slug: str) -> list[str]:
    try:
        row = _first_row(
            supabase.table("workspace_branding")
            .select("enabled_workflows")
            .eq("workspace_slug", workspace_slug)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning("onboarding branding lookup failed workspace=%s: %s", workspace_slug, exc)
        return []
    values = row.get("enabled_workflows") if row else []
    return [str(value) for value in values] if isinstance(values, list) else []


def _load_operational_workflows(workspace_slug: str) -> list[str]:
    """Read existing workflow rows without changing or inferring their settings."""
    try:
        result = (
            supabase.table("workspace_workflow_settings")
            .select("workflow_type")
            .eq("workspace_slug", workspace_slug)
            .execute()
        )
    except Exception as exc:
        logger.warning("onboarding workflow inventory failed workspace=%s: %s", workspace_slug, exc)
        return []
    configured = {
        str(row.get("workflow_type") or "")
        for row in (getattr(result, "data", None) or [])
        if row.get("workflow_type") != ONBOARDING_WORKFLOW_TYPE
    }
    return [workflow for workflow in _active_workflows() if workflow in configured]


def _initial_data(workspace_slug: str) -> Dict[str, Any]:
    workspace = _load_workspace(workspace_slug)
    entitlements = load_workspace_entitlements(workspace_slug, client=supabase)
    plan_id = entitlements.plan_id
    allowed = _allowed_workflows(entitlements)
    self_service = is_self_service_workspace(workspace_slug, client=supabase)
    provisioned = _load_provisioned_workflows(workspace_slug)
    if not self_service:
        managed_access = set(allowed) | set(provisioned) | set(_load_operational_workflows(workspace_slug))
        allowed = [workflow for workflow in _active_workflows() if workflow in managed_access]
    row = _load_settings(workspace_slug) or {}
    extra = row.get("extra_settings") if isinstance(row.get("extra_settings"), dict) else {}
    onboarding = extra.get("onboarding") if isinstance(extra.get("onboarding"), dict) else {}
    stored_profile = onboarding.get("profile") if isinstance(onboarding.get("profile"), dict) else {}
    profile = _normalize_profile(
        stored_profile,
        allowed_workflows=allowed,
        fallback_workflows=allowed,
    )
    return {
        "workspaceSlug": workspace_slug,
        "workspaceName": str(workspace.get("name") or workspace_slug),
        "planId": plan_id,
        "accessMode": entitlements.access_mode,
        "selfService": self_service,
        "provisioningMode": "self_service" if self_service else "profile_only",
        "allowedWorkflowTypes": allowed,
        "enabledWorkflowTypes": profile["workflowTypes"],
        "provisionedWorkflowTypes": provisioned,
        "profile": profile,
        "completedAt": onboarding.get("completed_at") if isinstance(onboarding.get("completed_at"), str) else None,
    }


def _preview(workspace_slug: str, profile: OnboardingProfile) -> Dict[str, Any]:
    current = _initial_data(workspace_slug)
    normalized = _normalize_profile(
        profile,
        allowed_workflows=current["allowedWorkflowTypes"],
        fallback_workflows=current["enabledWorkflowTypes"],
    )
    token, expires_at = _issue_preview_token(workspace_slug, normalized)
    warnings = []
    warning_codes = []
    if current["selfService"] and current["planId"] == "free":
        warning_codes.append("free_asset_retention_60_days")
        warnings.append(
            f"No plano Free, os assets ficam disponíveis por {FREE_ASSET_RETENTION_DAYS} dias; "
            "os metadados e a auditoria permanecem registrados."
        )
    if current["provisioningMode"] == "profile_only":
        warning_codes.append("managed_profile_only")
        warnings.append(
            "Este é um workspace gerenciado. O MotoSchema salvará apenas o perfil e não alterará "
            "formulários, integrações ou workflows já ativos."
        )
    return {
        "workspaceSlug": workspace_slug,
        "planId": current["planId"],
        "accessMode": current["accessMode"],
        "selfService": current["selfService"],
        "provisioningMode": current["provisioningMode"],
        "profile": normalized,
        "enabledWorkflows": normalized["workflowTypes"],
        "changes": [
            {
                "key": "operation",
                "title": "Perfil operacional",
                "detail": (
                    f"{normalized['operationType']} · equipe {normalized['teamSize']} · "
                    f"{normalized['monthlyVolume']} operações/mês"
                ),
            },
            {
                "key": "workflows",
                "title": "Acesso aos workflows",
                "detail": ", ".join(normalized["workflowTypes"]),
            },
            {
                "key": "integrations",
                "title": "Prioridades de integração",
                "detail": ", ".join(normalized["integrations"]) or "Configurar depois",
            },
            {
                "key": "governance",
                "title": "Governança do MotoSchema",
                "detail": "Prévia assinada, confirmação humana e registro de auditoria antes da aplicação.",
            },
            {
                "key": "provisioning",
                "title": "Aplicação segura",
                "detail": (
                    "Os workflows selecionados serão ativados; integrações externas aguardam autorização."
                    if current["provisioningMode"] == "self_service"
                    else "Somente o perfil será atualizado; a operação existente permanecerá intacta."
                ),
            },
        ],
        "warnings": warnings,
        "warningCodes": warning_codes,
        "previewToken": token,
        "expiresAt": expires_at,
    }


def _create_audit(
    workspace_slug: str,
    operation: str,
    profile: Dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    try:
        result = (
            supabase.table("setup_ai_action_audit")
            .insert(
                {
                    "workspace_slug": workspace_slug,
                    "workflow_type": ONBOARDING_WORKFLOW_TYPE,
                    "surface": "portal_motoschema",
                    "action_type": "configure_onboarding",
                    "operation": operation,
                    "status": "requested",
                    "request_payload": {"operation": operation, "profile": profile},
                    "dry_run": operation == "preview_patch",
                    "confirmed": operation == "apply_patch",
                }
            )
            .execute()
        )
        row = _first_row(result) or {}
        return (str(row.get("id")) if row.get("id") else None), None
    except Exception as exc:
        logger.warning("onboarding audit unavailable workspace=%s: %s", workspace_slug, exc)
        return None, str(exc)


def _finish_audit(
    audit_id: Optional[str],
    *,
    status: str,
    response: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    if not audit_id:
        return
    try:
        (
            supabase.table("setup_ai_action_audit")
            .update(
                {
                    "status": status,
                    "backend_response": response,
                    "error_message": error,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", audit_id)
            .execute()
        )
    except Exception:
        logger.exception("onboarding audit finalization failed audit_id=%s", audit_id)


@router.get("/{workspace_slug}/onboarding")
async def get_onboarding(
    workspace_slug: str,
    x_portal_token: Optional[str] = Header(default=None),
):
    slug = workspace_slug.strip().lower()
    require_portal_session(slug, x_portal_token)
    return {"ok": True, "data": _initial_data(slug)}


@router.post("/{workspace_slug}/onboarding")
async def configure_onboarding(
    workspace_slug: str,
    body: OnboardingAction,
    x_portal_token: Optional[str] = Header(default=None),
):
    slug = workspace_slug.strip().lower()
    require_portal_session(slug, x_portal_token)
    preview = _preview(slug, body.profile)
    audit_id, audit_error = _create_audit(slug, body.operation, preview["profile"])

    if body.operation == "preview_patch":
        _finish_audit(audit_id, status="succeeded", response=preview)
        return {
            "ok": True,
            "data": preview,
            "audit_id": audit_id,
            "audit_warning": audit_error,
        }

    if not audit_id:
        raise HTTPException(
            status_code=503,
            detail="A auditoria está indisponível; a aplicação foi bloqueada.",
        )
    if not body.preview_token or not _valid_preview_token(body.preview_token, slug, preview["profile"]):
        _finish_audit(audit_id, status="blocked", error="invalid or expired preview")
        raise HTTPException(
            status_code=409,
            detail="A prévia expirou ou não corresponde à configuração atual. Gere uma nova prévia.",
        )

    current_row = _load_settings(slug) or {}
    extra = copy.deepcopy(current_row.get("extra_settings") or {})
    completed_at = datetime.now(timezone.utc).isoformat()
    extra["onboarding"] = {
        "version": 1,
        "profile": preview["profile"],
        "enabled_workflows": preview["enabledWorkflows"],
        "preview_digest": _profile_digest(slug, preview["profile"]),
        "completed_at": completed_at,
        "completed_by": "portal_session",
        "provisioning": {
            "mode": preview["provisioningMode"],
            "workflow_status": (
                "active" if preview["provisioningMode"] == "self_service" else "unchanged"
            ),
            "integrations": {
                integration: "pending_authorization"
                for integration in preview["profile"]["integrations"]
            },
            "applied_at": completed_at,
        },
    }
    try:
        if preview["provisioningMode"] == "self_service":
            (
                supabase.table("workspace_branding")
                .update({"enabled_workflows": preview["enabledWorkflows"]})
                .eq("workspace_slug", slug)
                .execute()
            )
        (
            supabase.table("workspace_workflow_settings")
            .upsert(
                {
                    "workspace_slug": slug,
                    "workflow_type": ONBOARDING_WORKFLOW_TYPE,
                    "extra_settings": extra,
                },
                on_conflict="workspace_slug,workflow_type",
            )
            .execute()
        )
    except Exception as exc:
        logger.exception("onboarding apply failed workspace=%s", slug)
        _finish_audit(audit_id, status="failed", error=str(exc))
        raise HTTPException(status_code=503, detail="Não foi possível salvar o onboarding.") from exc

    result = {**preview, "completedAt": completed_at}
    _finish_audit(audit_id, status="succeeded", response=result)
    return {"ok": True, "data": result, "audit_id": audit_id, "audit_warning": None}
