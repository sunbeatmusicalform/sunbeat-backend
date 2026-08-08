"""Delete expired Free-plan objects. Dry-run is the default and safest mode."""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.database import supabase

logger = logging.getLogger("sunbeat.asset_retention_job")


def _event(event: str, **values: Any) -> None:
    logger.info(json.dumps({"event": event, **values}, sort_keys=True, default=str))


def enforce_expired_assets(*, apply: bool = False, limit: int = 100, database: Any = None) -> dict[str, int]:
    client = database or supabase
    now = datetime.now(timezone.utc).isoformat()
    result = (
        client.table("asset_retention_records")
        .select("id,workspace_slug,storage_bucket,storage_path,storage_status,deletion_attempts,expires_at")
        .is_("deleted_at", "null")
        .lte("expires_at", now)
        .order("expires_at")
        .limit(limit)
        .execute()
    )
    records = getattr(result, "data", None) or []
    summary = {"eligible": len(records), "deleted": 0, "missing": 0, "failed": 0}
    for record in records:
        base = {
            "asset_id": record["id"],
            "workspace_slug": record["workspace_slug"],
            "storage_bucket": record["storage_bucket"],
            "storage_path": record["storage_path"],
            "dry_run": not apply,
        }
        if not apply:
            _event("asset_retention_eligible", **base)
            continue
        try:
            client.storage.from_(record["storage_bucket"]).remove([record["storage_path"]])
            status = "deleted"
            summary["deleted"] += 1
        except Exception as exc:
            message = str(exc)
            if "not found" in message.lower() or "404" in message:
                status = "missing"
                summary["missing"] += 1
            else:
                summary["failed"] += 1
                (
                    client.table("asset_retention_records")
                    .update(
                        {
                            "storage_status": "error",
                            "deletion_attempts": int(record.get("deletion_attempts") or 0) + 1,
                            "last_error": type(exc).__name__,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    .eq("id", record["id"])
                    .is_("deleted_at", "null")
                    .execute()
                )
                _event("asset_retention_failed", error=type(exc).__name__, **base)
                continue
        deleted_at = datetime.now(timezone.utc).isoformat()
        (
            client.table("asset_retention_records")
            .update(
                {
                    "storage_status": status,
                    "deletion_attempts": int(record.get("deletion_attempts") or 0) + 1,
                    "last_error": None,
                    "deleted_at": deleted_at,
                    "updated_at": deleted_at,
                }
            )
            .eq("id", record["id"])
            .is_("deleted_at", "null")
            .execute()
        )
        _event("asset_retention_deleted", status=status, **base)
    _event("asset_retention_summary", **summary, dry_run=not apply)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="perform deletion; omitted means dry-run")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    enforce_expired_assets(apply=args.apply, limit=max(1, min(args.limit, 1000)))


if __name__ == "__main__":
    main()
