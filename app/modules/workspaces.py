from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from app.core.database import supabase
from app.modules.workflow_registry import (
    list_registered_workflows,
    resolve_workflow_identity,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _matches_workflow_scope(
    field_override: Dict[str, Any],
    workflow_identity: Dict[str, str],
) -> bool:
    step_key = str(field_override.get("step_key") or "").strip()
    if step_key in {"__workspace_settings__", "__workspace_security__"}:
        return False

    workflow_type = str(field_override.get("workflow_type") or "").strip()
    if workflow_type and workflow_type != workflow_identity["workflow_type"]:
        return False

    form_version = str(field_override.get("form_version") or "").strip()
    if form_version and form_version != workflow_identity["form_version"]:
        return False

    return True


def _workflow_scope_priority(
    field_override: Dict[str, Any],
    workflow_identity: Dict[str, str],
) -> int:
    priority = 0

    workflow_type = str(field_override.get("workflow_type") or "").strip()
    if workflow_type == workflow_identity["workflow_type"]:
        priority += 2

    form_version = str(field_override.get("form_version") or "").strip()
    if form_version == workflow_identity["form_version"]:
        priority += 1

    return priority


def _dedupe_scoped_field_overrides(
    field_overrides: List[Dict[str, Any]],
    workflow_identity: Dict[str, str],
) -> List[Dict[str, Any]]:
    selected: Dict[str, Dict[str, Any]] = {}

    for index, field_override in enumerate(field_overrides):
        if not _matches_workflow_scope(field_override, workflow_identity):
            continue

        dedupe_key = (
            f"{str(field_override.get('step_key') or '').strip()}:"
            f"{str(field_override.get('field_key') or '').strip()}"
        )
        candidate = {
            "item": field_override,
            "priority": _workflow_scope_priority(field_override, workflow_identity),
            "index": index,
        }
        existing = selected.get(dedupe_key)

        if (
            existing is None
            or candidate["priority"] > existing["priority"]
            or (
                candidate["priority"] == existing["priority"]
                and candidate["index"] > existing["index"]
            )
        ):
            selected[dedupe_key] = candidate

    result = [entry["item"] for entry in selected.values()]
    result.sort(
        key=lambda item: (
            str(item.get("step_key") or ""),
            item.get("sort_order") if item.get("sort_order") is not None else 9999,
            str(item.get("created_at") or ""),
        )
    )
    return result


async def _load_workspace_config(
    workspace_slug: str,
    workflow_type: Optional[str] = None,
    form_version: Optional[str] = None,
):
    workflow_identity = resolve_workflow_identity(
        workspace_slug=workspace_slug,
        workflow_type=workflow_type,
        form_version=form_version,
    )

    try:
        branding_res = (
            supabase.table("workspace_branding")
            .select("*")
            .eq("workspace_slug", workspace_slug)
            .limit(1)
            .execute()
        )

        fields_res = (
            supabase.table("workspace_field_overrides")
            .select("*")
            .eq("workspace_slug", workspace_slug)
            .order("sort_order", desc=False)
            .execute()
        )

        airtable_res = (
            supabase.table("workspace_airtable_mapping")
            .select("*")
            .eq("workspace_slug", workspace_slug)
            .eq("is_enabled", True)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load workspace config: {exc}")

    branding = branding_res.data[0] if branding_res.data else None
    scoped_field_overrides = _dedupe_scoped_field_overrides(
        fields_res.data or [],
        workflow_identity,
    )

    return {
        "ok": True,
        "workspace_slug": workspace_slug,
        "workflow_type": workflow_identity["workflow_type"],
        "form_version": workflow_identity["form_version"],
        "workflow_status": workflow_identity["status"],
        "workflow_renderer": workflow_identity["renderer"],
        "template_factory": workflow_identity["template_factory"],
        "payload_builder": workflow_identity["payload_builder"],
        "public_path_prefix": workflow_identity["public_path_prefix"],
        "available_workflows": list_registered_workflows(),
        "branding": branding,
        "field_overrides": scoped_field_overrides,
        "airtable_mapping": airtable_res.data or [],
    }


@router.get("/{workspace_slug}/workflow-config")
async def get_workspace_workflow_config(
    workspace_slug: str,
    workflow_type: Optional[str] = None,
    form_version: Optional[str] = None,
):
    return await _load_workspace_config(
        workspace_slug=workspace_slug,
        workflow_type=workflow_type,
        form_version=form_version,
    )


@router.get("/{workspace_slug}/release-intake-config")
async def get_release_intake_config(
    workspace_slug: str,
    workflow_type: Optional[str] = None,
    form_version: Optional[str] = None,
):
    return await _load_workspace_config(
        workspace_slug=workspace_slug,
        workflow_type=workflow_type,
        form_version=form_version,
    )
