"""Asset-retention registry and access enforcement for the Free plan."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from app.core.database import supabase
from app.services.self_service_entitlements import (
    is_self_service_workspace,
    load_workspace_entitlements,
)

logger = logging.getLogger("sunbeat.asset_retention")
FREE_RETENTION_DAYS = 60


def retention_days_for_workspace(workspace_slug: str, *, database: Any = None) -> int | None:
    client = database or supabase
    if not is_self_service_workspace(workspace_slug, client=client, fail_closed=True):
        return None
    entitlements = load_workspace_entitlements(workspace_slug, client=client)
    return FREE_RETENTION_DAYS if entitlements.plan_id == "free" else None


def register_asset(
    *,
    workspace_slug: str,
    draft_token: str,
    storage_bucket: str,
    storage_path: str,
    file_name: str,
    mime_type: str,
    size_bytes: int,
    status: str,
    content_sha256: str | None = None,
    database: Any = None,
) -> dict[str, Any] | None:
    client = database or supabase
    retention_days = retention_days_for_workspace(workspace_slug, database=client)
    if retention_days is None:
        return None
    created_at = datetime.now(timezone.utc)
    record = {
        "id": str(uuid4()),
        "workspace_slug": workspace_slug,
        "draft_token_hash": hashlib.sha256(draft_token.encode()).hexdigest(),
        "storage_bucket": storage_bucket,
        "storage_path": storage_path,
        "file_name": file_name,
        "mime_type": mime_type or "application/octet-stream",
        "size_bytes": size_bytes,
        "content_sha256": content_sha256,
        "retention_days": retention_days,
        "expires_at": (created_at + timedelta(days=retention_days)).isoformat(),
        "storage_status": status,
        "created_at": created_at.isoformat(),
    }
    try:
        client.table("asset_retention_records").insert(record).execute()
    except Exception as exc:
        logger.exception(
            "asset retention registration failed workspace=%s bucket=%s path=%s",
            workspace_slug,
            storage_bucket,
            storage_path,
        )
        raise HTTPException(status_code=503, detail="Asset retention tracking is unavailable") from exc
    return record


def assert_asset_not_expired(
    *, storage_bucket: str, storage_path: str, database: Any = None
) -> None:
    client = database or supabase
    try:
        result = (
            client.table("asset_retention_records")
            .select("expires_at,deleted_at,storage_status")
            .eq("storage_bucket", storage_bucket)
            .eq("storage_path", storage_path)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.exception("asset retention access check failed bucket=%s path=%s", storage_bucket, storage_path)
        raise HTTPException(status_code=503, detail="Asset retention check is unavailable") from exc
    rows = getattr(result, "data", None) or []
    if not rows:
        return
    record = rows[0]
    expires_at = datetime.fromisoformat(str(record["expires_at"]).replace("Z", "+00:00"))
    if record.get("deleted_at") or expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="This asset has expired under the Free retention policy.")
