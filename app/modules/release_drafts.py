from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.core.database import supabase
from app.modules.workflow_registry import (
    build_workflow_source,
    normalize_workflow_type,
    resolve_workflow_identity,
)
from app.services.email import send_draft_link_email

router = APIRouter(prefix="/release-drafts", tags=["release_drafts"])
logger = logging.getLogger("sunbeat.release_drafts")
DEFAULT_WORKSPACE_SLUG = "atabaque"


def _get_draft_contact(values: Dict[str, Any]) -> Dict[str, str]:
    identification = values.get("identification") or {}
    requester = values.get("requester_identification") or {}
    project_context = values.get("project_context") or {}

    return {
        "submitter_email": str(
            identification.get("submitter_email")
            or requester.get("requester_email")
            or ""
        ).strip(),
        "submitter_name": str(
            identification.get("submitter_name")
            or requester.get("requester_name")
            or ""
        ).strip(),
        "project_title": str(
            identification.get("project_title")
            or project_context.get("project_title")
            or ""
        ).strip(),
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_draft_row(draft_token: str) -> Dict[str, Any] | None:
    result = (
        supabase.table("release_intake_drafts")
        .select("*")
        .eq("draft_token", draft_token)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _draft_meta(existing: Dict[str, Any] | None, payload_meta: Dict[str, Any] | None) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    if existing and isinstance(existing.get("meta"), dict):
        meta.update(existing["meta"])
    if payload_meta and isinstance(payload_meta, dict):
        meta.update(payload_meta)
    return meta


def _ensure_identity_meta(
    meta: Dict[str, Any],
    *,
    workspace_slug: str,
    workflow_type: Any,
) -> Dict[str, Any]:
    normalized = dict(meta)
    resolved_identity = resolve_workflow_identity(
        workspace_slug=workspace_slug,
        workflow_type=workflow_type or normalized.get("workflow_type"),
        form_version=normalized.get("form_version"),
    )

    normalized["workflow_type"] = resolved_identity["workflow_type"]
    normalized["form_version"] = resolved_identity["form_version"]
    normalized["source"] = str(normalized.get("source") or "").strip() or build_workflow_source(
        workspace_slug,
        resolved_identity["workflow_type"],
        resolved_identity["form_version"],
    )

    return normalized


@router.post("/save")
async def save_draft(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft_token = payload.get("draft_token") or str(uuid4())
    values = payload.get("values") or {}
    contact = _get_draft_contact(values)
    now_iso = utc_now_iso()

    existing = _load_draft_row(draft_token) or {}
    workspace_slug = payload.get("workspace_slug") or existing.get("client_slug") or DEFAULT_WORKSPACE_SLUG
    meta = _ensure_identity_meta(
        _draft_meta(existing, payload.get("meta") or {}),
        workspace_slug=workspace_slug,
        workflow_type=payload.get("workflow_type"),
    )

    row = {
        "draft_token": draft_token,
        "client_slug": workspace_slug,
        "submitter_email": contact["submitter_email"] or existing.get("submitter_email"),
        "submitter_name": contact["submitter_name"] or existing.get("submitter_name"),
        "current_step": payload.get("current_step") or existing.get("current_step") or "intro",
        "progress_percent": payload.get("progress_percent") or 0,
        "values": values,
        "meta": meta,
        "status": existing.get("status") or "draft",
        "updated_at": now_iso,
    }

    try:
        if existing:
            (
                supabase.table("release_intake_drafts")
                .update(row)
                .eq("draft_token", draft_token)
                .execute()
            )
        else:
            row["created_at"] = now_iso
            supabase.table("release_intake_drafts").insert(row).execute()
    except Exception as exc:
        logger.exception("Draft save failed")
        raise HTTPException(status_code=500, detail=f"Falha ao salvar rascunho: {exc}")

    saved = _load_draft_row(draft_token)
    if not saved:
        raise HTTPException(status_code=500, detail="Draft was not persisted")

    saved_meta = _ensure_identity_meta(
        saved.get("meta") or {},
        workspace_slug=saved.get("client_slug") or workspace_slug,
        workflow_type=(saved.get("meta") or {}).get("workflow_type"),
    )
    return {
        "ok": True,
        "draft_token": draft_token,
        "updated_at": saved.get("updated_at"),
        "draft_link_email_sent": bool(saved_meta.get("draft_link_email_sent")),
        "draft_link_email_sent_at": saved_meta.get("draft_link_email_sent_at"),
    }


@router.get("/{draft_token}")
async def get_draft(draft_token: str) -> Dict[str, Any]:
    draft = _load_draft_row(draft_token)

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    meta = _ensure_identity_meta(
        draft.get("meta") or {},
        workspace_slug=draft.get("client_slug") or DEFAULT_WORKSPACE_SLUG,
        workflow_type=(draft.get("meta") or {}).get("workflow_type"),
    )
    return {
        "ok": True,
        "draft_token": draft_token,
        "updated_at": draft.get("updated_at"),
        "draft_link_email_sent": bool(meta.get("draft_link_email_sent")),
        "draft_link_email_sent_at": meta.get("draft_link_email_sent_at"),
        "data": {
            "workspace_slug": draft.get("client_slug"),
            "workflow_type": normalize_workflow_type(meta.get("workflow_type")),
            "current_step": draft.get("current_step"),
            "progress_percent": draft.get("progress_percent"),
            "values": draft.get("values") or {},
            "meta": meta,
        },
    }


@router.post("/send-link")
async def send_draft_link(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft_token = payload.get("draft_token")
    to_email = payload.get("to_email")
    recipient_name = payload.get("recipient_name")
    project_title = payload.get("project_title")

    if not draft_token:
        raise HTTPException(status_code=400, detail="draft_token is required")

    if not to_email:
        raise HTTPException(status_code=400, detail="to_email is required")

    draft = _load_draft_row(draft_token)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    meta = draft.get("meta") or {}
    workspace_slug = payload.get("workspace_slug") or draft.get("client_slug") or DEFAULT_WORKSPACE_SLUG
    workflow_type = normalize_workflow_type(
        payload.get("workflow_type") or meta.get("workflow_type")
    )
    if meta.get("draft_link_email_sent"):
        return {
            "ok": True,
            "already_sent": True,
            "message": "Draft link email already sent",
            "draft_token": draft_token,
            "draft_link_email_sent": True,
            "draft_link_email_sent_at": meta.get("draft_link_email_sent_at"),
        }

    try:
        supports_workflow_routing = (
            "workflow_type" in inspect.signature(send_draft_link_email).parameters
        )
        if workflow_type != "release_intake" and not supports_workflow_routing:
            raise HTTPException(
                status_code=409,
                detail=(
                    "O envio de link por email para este workflow ainda nao esta "
                    "habilitado nesta instancia do backend."
                ),
            )

        email_kwargs = {
            "to_email": to_email,
            "draft_token": draft_token,
            "project_title": project_title,
            "recipient_name": recipient_name,
            "workspace_slug": workspace_slug,
        }
        if supports_workflow_routing:
            email_kwargs["workflow_type"] = workflow_type

        result = send_draft_link_email(
            **email_kwargs,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Draft email failed")
        raise HTTPException(status_code=500, detail=f"Falha ao enviar email do rascunho: {exc}")

    sent_at = utc_now_iso()
    updated_meta = _ensure_identity_meta(
        _draft_meta(draft, None),
        workspace_slug=workspace_slug,
        workflow_type=(draft.get("meta") or {}).get("workflow_type"),
    )
    updated_meta.update(
        {
            "draft_link_email_sent": True,
            "draft_link_email_sent_at": sent_at,
        }
    )

    try:
        (
            supabase.table("release_intake_drafts")
            .update(
                {
                    "meta": updated_meta,
                    "updated_at": sent_at,
                }
            )
            .eq("draft_token", draft_token)
            .execute()
        )
    except Exception as exc:
        logger.exception("Draft email state update failed")
        raise HTTPException(status_code=500, detail=f"Falha ao registrar envio do rascunho: {exc}")

    return {
        "ok": True,
        "already_sent": False,
        "message": "Draft link email sent successfully",
        "draft_token": draft_token,
        "draft_link_email_sent": True,
        "draft_link_email_sent_at": sent_at,
        "email_result": result,
    }
