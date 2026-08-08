from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import HTTPException

from app.core.database import supabase

logger = logging.getLogger("sunbeat.self_service_entitlements")

PLAN_WORKFLOWS: dict[str, set[str] | None] = {
    "free": {"release_intake"},
    "starter": {"release_intake", "rights_clearance"},
    "pro": {
        "release_intake",
        "rights_clearance",
        "company_registry",
        "people_registry",
    },
    "enterprise": None,
    "enterprise_core": None,
    "enterprise_ops": None,
    "enterprise_distribution": None,
}


@dataclass(frozen=True)
class WorkspaceEntitlements:
    """Resolved access for a workspace after applying any explicit override."""

    plan_id: str
    max_submissions_month: int | None
    enabled_workflow_types: set[str] | None
    access_mode: Literal["plan", "custom"]


def _first_row(result: Any) -> dict[str, Any] | None:
    rows = getattr(result, "data", None) or []
    return rows[0] if rows else None


def is_self_service_workspace(
    workspace_slug: str,
    *,
    client: Any = None,
    fail_closed: bool = False,
) -> bool:
    """Return True only for accounts explicitly marked by the new signup flow."""
    database = client or supabase
    try:
        membership = _first_row(
            database.table("workspace_users")
            .select("user_id")
            .eq("workspace_slug", workspace_slug)
            .eq("role", "owner")
            .limit(1)
            .execute()
        )
        if not membership:
            return False

        auth_result = database.auth.admin.get_user_by_id(str(membership["user_id"]))
        user = getattr(auth_result, "user", None)
        metadata = getattr(user, "user_metadata", None) or {}
        return metadata.get("self_service") is True
    except Exception as exc:
        logger.warning(
            "Could not verify self-service ownership workspace=%s; legacy behavior preserved",
            workspace_slug,
            exc_info=True,
        )
        if fail_closed:
            raise HTTPException(
                status_code=503,
                detail="Workspace retention policy is unavailable",
            ) from exc
        return False


def load_workspace_entitlements(
    workspace_slug: str, *, client: Any = None
) -> WorkspaceEntitlements:
    """Resolve the canonical plan plus tenant-specific commercial overrides.

    This is intentionally shared by submission enforcement and MotoSchema so the
    portal can never advertise a different access map from the API guard.
    """
    database = client or supabase
    workspace = _first_row(
        database.table("workspaces")
        .select("plan_id, plans(submissions_month)")
        .eq("slug", workspace_slug)
        .limit(1)
        .execute()
    )
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    plan_id = str(workspace.get("plan_id") or "free")
    plan = workspace.get("plans") or {}
    if isinstance(plan, list):
        plan = plan[0] if plan else {}
    raw_limit = plan.get("submissions_month") if isinstance(plan, dict) else None
    limit = int(raw_limit) if raw_limit is not None else None
    workflows = PLAN_WORKFLOWS.get(plan_id, {"release_intake"})

    try:
        override = _first_row(
            database.table("workspace_plan_overrides")
            .select("max_submissions_month, enabled_workflow_types")
            .eq("workspace_slug", workspace_slug)
            .limit(1)
            .execute()
        )
    except Exception:
        override = None
        logger.warning("Plan override unavailable workspace=%s", workspace_slug, exc_info=True)

    override_applied = False
    if override:
        if override.get("max_submissions_month") is not None:
            limit = int(override["max_submissions_month"])
            override_applied = True
        if isinstance(override.get("enabled_workflow_types"), list):
            workflows = {
                str(value).strip()
                for value in override["enabled_workflow_types"]
                if str(value).strip()
            }
            override_applied = True

    return WorkspaceEntitlements(
        plan_id=plan_id,
        max_submissions_month=limit,
        enabled_workflow_types=workflows,
        access_mode="custom" if override_applied else "plan",
    )


def _load_plan(workspace_slug: str) -> tuple[str, int | None, set[str] | None]:
    """Backward-compatible tuple used by older callers and tests."""
    resolved = load_workspace_entitlements(workspace_slug)
    return (
        resolved.plan_id,
        resolved.max_submissions_month,
        resolved.enabled_workflow_types,
    )


def enforce_self_service_submission_limits(
    *, workspace_slug: str, workflow_type: str
) -> None:
    if not is_self_service_workspace(workspace_slug):
        return

    _plan_id, limit, workflows = _load_plan(workspace_slug)
    if workflows is not None and workflow_type not in workflows:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "workflow_not_in_plan",
                "message": "This workflow is not included in the workspace plan.",
            },
        )

    if limit is None:
        return

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    try:
        result = (
            supabase.table("submissions")
            .select("id", count="exact", head=True)
            .eq("client_slug", workspace_slug)
            .not_.is_("submitted_at", "null")
            .gte("submitted_at", month_start.isoformat())
            .execute()
        )
        used = int(getattr(result, "count", 0) or 0)
    except Exception as exc:
        logger.exception("Submission quota unavailable workspace=%s", workspace_slug)
        raise HTTPException(status_code=503, detail="Submission quota is unavailable") from exc

    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "monthly_submission_limit_reached",
                "message": "The monthly submission limit has been reached.",
                "limit": limit,
                "used": used,
            },
        )
