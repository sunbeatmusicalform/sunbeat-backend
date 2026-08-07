"""Configuração e emissão administrativa de links de edição."""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from app.core.admin_auth import _admin_token_is_valid
from app.core.database import supabase
from app.modules.portal_session import require_portal_session
from app.services.edit_access import (
    DEFAULT_EDIT_POLICIES,
    EDIT_POLICIES,
    authorize_edit_token,
    get_edit_policy,
    save_edit_policy,
)
from app.services.email import build_workflow_edit_url, send_edit_link_email

router = APIRouter(prefix="/workspaces", tags=["edit-access"])
WORKFLOWS = tuple(DEFAULT_EDIT_POLICIES)


async def _require_portal_or_admin(
    workspace_slug: str,
    x_portal_token: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> None:
    if x_admin_token and _admin_token_is_valid(x_admin_token.strip()):
        return
    require_portal_session(workspace_slug, x_portal_token)


class EditConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: Literal["link_after_submit", "admin_authorized", "disabled"]


def _submission_candidates(slug: str) -> list[Dict[str, Any]]:
    result = (
        supabase.table("submissions")
        .select("id,client_slug,release_title,main_title,email,artist_name,created_at,payload")
        .eq("client_slug", slug)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )
    items = []
    for row in result.data or []:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if payload.get("workflow_type") != "company_registry":
            continue
        company = payload.get("company_data") or {}
        legal = payload.get("legal_representative") or {}
        items.append({
            "record_id": str(row.get("id") or ""),
            "workflow_type": "company_registry",
            "title": company.get("fantasy_name") or row.get("release_title") or "Empresa",
            "email": legal.get("email") or row.get("email") or "",
            "created_at": row.get("created_at"),
        })
    return items


def _people_candidates(slug: str) -> list[Dict[str, Any]]:
    result = (
        supabase.table("people_registry_records")
        .select("id,workspace_slug,display_name,email_primary,created_at")
        .eq("workspace_slug", slug)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )
    return [{
        "record_id": str(row.get("id") or ""),
        "workflow_type": "people_registry",
        "title": row.get("display_name") or "Pessoa",
        "email": row.get("email_primary") or "",
        "created_at": row.get("created_at"),
    } for row in (result.data or [])]


@router.get("/{workspace_slug}/edit-config")
async def get_edit_config(workspace_slug: str) -> Dict[str, Any]:
    slug = workspace_slug.strip().lower()
    return {
        "ok": True,
        "workspace_slug": slug,
        "workflows": {workflow: {"policy": get_edit_policy(slug, workflow)} for workflow in WORKFLOWS},
    }


@router.patch("/{workspace_slug}/workflows/{workflow_type}/edit-config")
async def patch_edit_config(
    workspace_slug: str,
    workflow_type: str,
    body: EditConfigPatch,
    _: None = Depends(_require_portal_or_admin),
) -> Dict[str, Any]:
    slug = workspace_slug.strip().lower()
    workflow = workflow_type.strip().lower()
    if workflow not in WORKFLOWS:
        raise HTTPException(status_code=404, detail="workflow desconhecido")
    save_edit_policy(slug, workflow, body.policy)
    return {"ok": True, "workspace_slug": slug, "workflow_type": workflow, "policy": get_edit_policy(slug, workflow)}


@router.get("/{workspace_slug}/edit-access")
async def list_edit_access(
    workspace_slug: str,
    _: None = Depends(_require_portal_or_admin),
) -> Dict[str, Any]:
    slug = workspace_slug.strip().lower()
    return {"ok": True, "items": [*_submission_candidates(slug), *_people_candidates(slug)]}


@router.post("/{workspace_slug}/edit-access/{workflow_type}/{record_id}")
async def issue_edit_access(
    workspace_slug: str,
    workflow_type: str,
    record_id: str,
    _: None = Depends(_require_portal_or_admin),
) -> Dict[str, Any]:
    slug = workspace_slug.strip().lower()
    workflow = workflow_type.strip().lower()
    if workflow not in {"company_registry", "people_registry"}:
        raise HTTPException(status_code=422, detail="este workflow não usa autorização administrativa")

    token = str(uuid4())
    if workflow == "company_registry":
        result = supabase.table("submissions").select("id,payload").eq("id", record_id).eq("client_slug", slug).limit(1).execute()
        row = (result.data or [None])[0]
        if not row:
            raise HTTPException(status_code=404, detail="submissão não encontrada")
        payload = row.get("payload") or {}
        company = payload.get("company_data") or {}
        legal = payload.get("legal_representative") or {}
        title, email, name = company.get("fantasy_name") or "Empresa", legal.get("email"), legal.get("name")
        supabase.table("submissions").update({"edit_token": token}).eq("id", record_id).execute()
    else:
        result = supabase.table("people_registry_records").select("id,display_name,email_primary").eq("id", record_id).eq("workspace_slug", slug).limit(1).execute()
        row = (result.data or [None])[0]
        if not row:
            raise HTTPException(status_code=404, detail="cadastro não encontrado")
        title = name = row.get("display_name") or "Pessoa"
        email = row.get("email_primary")
        supabase.table("people_registry_records").update({"edit_token": token}).eq("id", record_id).execute()

    if not email:
        raise HTTPException(status_code=422, detail="cadastro sem e-mail para entrega do link")
    authorize_edit_token(slug, workflow, record_id, token)
    email_result = send_edit_link_email(
        to_email=str(email), edit_token=token, project_title=str(title), recipient_name=str(name or ""),
        workspace_slug=slug, workflow_type=workflow, event="on_edit",
    )
    return {
        "ok": True,
        "record_id": record_id,
        "workflow_type": workflow,
        "to_email": email,
        "edit_url": build_workflow_edit_url(edit_token=token, workspace_slug=slug, workflow_type=workflow),
        "email_status": email_result.get("status"),
    }
