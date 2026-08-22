from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query

from app.core.admin_auth import require_internal_admin_token
from app.services.automation_outbox import automation_workspace_status, dispatch_due_events

router = APIRouter(prefix="/internal/automations", tags=["internal-automations"])


@router.get("/status", dependencies=[Depends(require_internal_admin_token)])
def get_automation_status(workspace_slug: str = Query(min_length=1, max_length=100)) -> Dict[str, Any]:
    return {"ok": True, "workspace_slug": workspace_slug.strip().lower(), **automation_workspace_status(workspace_slug)}


@router.post("/dispatch", dependencies=[Depends(require_internal_admin_token)])
def dispatch_automation_outbox(
    dry_run: bool = Query(default=True),
    workspace_slug: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=25, ge=1, le=100),
) -> Dict[str, Any]:
    return dispatch_due_events(workspace_slug=workspace_slug, limit=limit, dry_run=dry_run)
