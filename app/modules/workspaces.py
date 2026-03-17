from __future__ import annotations

from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException

from app.core.database import supabase

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("/{workspace_slug}/release-intake-config")
async def get_release_intake_config(workspace_slug: str):
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

    return {
        "ok": True,
        "workspace_slug": workspace_slug,
        "branding": branding,
        "field_overrides": fields_res.data or [],
        "airtable_mapping": airtable_res.data or [],
    }