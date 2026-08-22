from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from urllib.parse import urlparse
from uuid import uuid4

import requests

from app.core.config import settings
from app.core.database import supabase

logger = logging.getLogger("sunbeat.automation")
EVENT_SCHEMA_VERSION = "2026-08-22"


def _slug_set(raw: str) -> set[str]:
    return {item.strip().lower() for item in str(raw or "").split(",") if item.strip()}


def automation_workspace_status(workspace_slug: str) -> Dict[str, Any]:
    slug = workspace_slug.strip().lower()
    allowlist = _slug_set(settings.ACTIVEPIECES_WORKSPACE_ALLOWLIST)
    # The managed Atabaque operation is never eligible for this pilot, even if
    # an environment variable is accidentally emptied or changed.
    denylist = _slug_set(settings.ACTIVEPIECES_WORKSPACE_DENYLIST) | {"atabaque"}

    if not settings.ACTIVEPIECES_ENABLED:
        return {"configured": False, "status": "disabled", "reason": "feature_disabled"}
    if slug in denylist:
        return {"configured": False, "status": "blocked", "reason": "workspace_denied"}
    if slug not in allowlist:
        return {"configured": False, "status": "disabled", "reason": "workspace_not_allowlisted"}
    if not settings.ACTIVEPIECES_WEBHOOK_URL or not settings.ACTIVEPIECES_WEBHOOK_SECRET:
        return {"configured": False, "status": "degraded", "reason": "missing_webhook_configuration"}
    return {"configured": True, "status": "ready", "reason": None}


def automation_enabled_for_workspace(workspace_slug: str) -> bool:
    return automation_workspace_status(workspace_slug)["status"] == "ready"


def _event_envelope(
    *,
    event_id: str,
    workspace_slug: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": event_type,
        "workspace_slug": workspace_slug.strip().lower(),
        "entity": {"type": entity_type, "id": str(entity_id)},
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }


def enqueue_event(
    *,
    workspace_slug: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    slug = workspace_slug.strip().lower()
    if not automation_enabled_for_workspace(slug):
        return {"status": "disabled", "queued": False}

    idempotency_key = f"{slug}:{event_type}:{entity_type}:{entity_id}"
    event_id = str(uuid4())
    envelope = _event_envelope(
        event_id=event_id,
        workspace_slug=slug,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
    )
    row = {
        "id": event_id,
        "workspace_slug": slug,
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "idempotency_key": idempotency_key,
        "payload": envelope,
        "status": "pending",
    }
    try:
        result = supabase.table("automation_outbox").insert(row).execute()
        created = (getattr(result, "data", None) or [row])[0]
        return {"status": "queued", "queued": True, "event_id": created.get("id")}
    except Exception:
        # Replays must not create a second side effect. A conflict on the unique
        # idempotency key is treated as already queued; other failures are logged
        # and never break the core submission transaction.
        try:
            existing = (
                supabase.table("automation_outbox")
                .select("id,status")
                .eq("idempotency_key", idempotency_key)
                .limit(1)
                .execute()
            )
            rows = getattr(existing, "data", None) or []
            if rows:
                return {
                    "status": rows[0].get("status") or "queued",
                    "queued": True,
                    "event_id": rows[0].get("id"),
                    "replayed": True,
                }
        except Exception:
            pass
        logger.exception("automation outbox enqueue failed workspace=%s event=%s", slug, event_type)
        return {"status": "failed", "queued": False}


def _validated_webhook_url() -> str:
    raw = str(settings.ACTIVEPIECES_WEBHOOK_URL or "").strip()
    parsed = urlparse(raw)
    local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if (parsed.scheme != "https" and not local_http) or not parsed.hostname:
        raise ValueError("Activepieces webhook must use HTTPS (HTTP is allowed only on localhost)")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Activepieces webhook URL cannot contain credentials or a fragment")
    return raw


def _signed_headers(body: str, *, event_id: str, timestamp: str) -> Dict[str, str]:
    secret = str(settings.ACTIVEPIECES_WEBHOOK_SECRET or "").encode("utf-8")
    digest = hmac.new(secret, f"{timestamp}.{body}".encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": event_id,
        "X-Sunbeat-Event-Id": event_id,
        "X-Sunbeat-Timestamp": timestamp,
        "X-Sunbeat-Signature": f"sha256={digest}",
        "User-Agent": "Sunbeat-Automation-Outbox/1.0",
    }


def _retry_at(attempts: int) -> str:
    delay_seconds = min(60 * (2 ** max(attempts - 1, 0)), 6 * 60 * 60)
    return (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()


def deliver_claimed_event(row: Dict[str, Any]) -> Dict[str, Any]:
    event_id = str(row.get("id") or row.get("payload", {}).get("event_id") or "")
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    attempts = int(row.get("attempts") or 1)

    try:
        response = requests.post(
            _validated_webhook_url(),
            data=body.encode("utf-8"),
            headers=_signed_headers(body, event_id=event_id, timestamp=timestamp),
            timeout=max(1, min(settings.ACTIVEPIECES_TIMEOUT_SECONDS, 30)),
        )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"webhook returned HTTP {response.status_code}")
    except Exception as exc:
        dead_letter = attempts >= max(1, settings.ACTIVEPIECES_MAX_ATTEMPTS)
        update = {
            "status": "dead_letter" if dead_letter else "failed",
            "last_error": str(exc)[:500],
            "next_attempt_at": _retry_at(attempts),
            "locked_at": None,
            "worker_id": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        supabase.table("automation_outbox").update(update).eq("id", row["id"]).execute()
        return {"id": row.get("id"), "status": update["status"], "error": update["last_error"]}

    update = {
        "status": "delivered",
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "last_error": None,
        "locked_at": None,
        "worker_id": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    supabase.table("automation_outbox").update(update).eq("id", row["id"]).execute()
    return {"id": row.get("id"), "status": "delivered"}


def dispatch_due_events(
    *,
    workspace_slug: str,
    limit: int = 25,
    dry_run: bool = True,
) -> Dict[str, Any]:
    slug = workspace_slug.strip().lower()
    if not automation_enabled_for_workspace(slug):
        return {"ok": True, "dry_run": dry_run, "claimed": 0, "results": [], "status": "disabled"}

    if dry_run:
        query = (
            supabase.table("automation_outbox")
            .select("id,workspace_slug,event_type,status,attempts,next_attempt_at,created_at")
            .in_("status", ["pending", "failed"])
            .lte("next_attempt_at", datetime.now(timezone.utc).isoformat())
            .order("created_at")
            .limit(max(1, min(limit, 100)))
        )
        query = query.eq("workspace_slug", slug)
        rows = getattr(query.execute(), "data", None) or []
        return {"ok": True, "dry_run": True, "claimed": 0, "candidates": rows, "results": []}

    worker_id = f"api-{uuid4()}"
    result = supabase.rpc(
        "claim_automation_outbox",
        {"p_worker_id": worker_id, "p_limit": max(1, min(limit, 100)), "p_workspace_slug": slug},
    ).execute()
    rows = getattr(result, "data", None) or []
    delivered = [deliver_claimed_event(row) for row in rows]
    return {"ok": True, "dry_run": False, "claimed": len(rows), "results": delivered}
