"""Read-only LGPD access inventory restricted to disposable QA workspaces."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


QA_WORKSPACE_PATTERN = re.compile(r"^(?:sunbeat-)?qa-[a-z0-9][a-z0-9-]{1,62}$")
SENSITIVE_KEY_PARTS = ("token", "secret", "password", "authorization", "hash")

WORKSPACE_TABLES: tuple[tuple[str, str], ...] = (
    ("workspace_branding", "workspace_slug"),
    ("workspace_workflow_settings", "workspace_slug"),
    ("workspace_plan_overrides", "workspace_slug"),
    ("self_service_magic_links", "workspace_slug"),
    ("portal_sessions", "workspace_slug"),
    ("asset_retention_records", "workspace_slug"),
    ("setup_ai_action_audit", "workspace_slug"),
    ("people_registry_records", "workspace_slug"),
    ("release_intake_drafts", "client_slug"),
    ("submissions", "client_slug"),
)


class SubjectAccessError(RuntimeError):
    """Raised when the access inventory cannot prove its QA/self-service boundary."""


def _rows(result: Any) -> list[dict[str, Any]]:
    value = getattr(result, "data", None)
    return value if isinstance(value, list) else []


def _first(result: Any) -> dict[str, Any] | None:
    rows = _rows(result)
    return rows[0] if rows else None


def _redact(value: Any) -> Any:
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS) else _redact(item)
            for key, item in value.items()
        }
    return value


def _query_workspace_rows(database: Any, table: str, field: str, workspace_slug: str) -> list[dict[str, Any]]:
    result = database.table(table).select("*").eq(field, workspace_slug).limit(500).execute()
    return _rows(result)


def build_qa_subject_access_report(
    database: Any,
    *,
    workspace_slug: str,
    email: str,
    request_id: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a data-access report without mutating any database or Storage row."""
    slug = workspace_slug.strip().lower()
    subject_email = email.strip().lower()
    normalized_request_id = request_id.strip()

    if slug == "atabaque" or not QA_WORKSPACE_PATTERN.fullmatch(slug):
        raise SubjectAccessError("Access reports are restricted to isolated QA workspaces.")
    if not subject_email or not normalized_request_id:
        raise SubjectAccessError("Subject email and request ID are required.")

    workspace = _first(
        database.table("workspaces")
        .select("*")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    if not workspace:
        raise SubjectAccessError("QA workspace was not found.")
    if str(workspace.get("owner_email") or "").strip().lower() != subject_email:
        raise SubjectAccessError("Subject email does not match the QA workspace owner.")

    membership = _first(
        database.table("workspace_users")
        .select("*")
        .eq("workspace_slug", slug)
        .eq("role", "owner")
        .limit(1)
        .execute()
    )
    if not membership or not membership.get("user_id"):
        raise SubjectAccessError("QA owner membership was not found.")

    auth_result = database.auth.admin.get_user_by_id(str(membership["user_id"]))
    auth_user = getattr(auth_result, "user", None)
    app_metadata = getattr(auth_user, "app_metadata", None) or {}
    auth_email = str(getattr(auth_user, "email", "") or "").strip().lower()
    if app_metadata.get("self_service") is not True:
        raise SubjectAccessError("Managed workspaces cannot use the QA access-report procedure.")
    if auth_email != subject_email:
        raise SubjectAccessError("Auth identity does not match the requested subject.")

    records: dict[str, dict[str, Any]] = {
        "workspaces": {"count": 1, "rows": [_redact(workspace)]},
        "workspace_users": {"count": 1, "rows": [_redact(membership)]},
    }
    for table, field in WORKSPACE_TABLES:
        table_rows = _query_workspace_rows(database, table, field, slug)
        records[table] = {"count": len(table_rows), "rows": _redact(table_rows)}

    timestamp = generated_at or datetime.now(timezone.utc)
    return {
        "request_id": normalized_request_id,
        "action": "access",
        "mode": "read_only",
        "generated_at": timestamp.astimezone(timezone.utc).isoformat(),
        "workspace_slug": slug,
        "subject": {
            "user_id": str(membership["user_id"]),
            "email": auth_email,
            "user_metadata": _redact(getattr(auth_user, "user_metadata", None) or {}),
            "app_metadata": _redact(app_metadata),
        },
        "records": records,
        "record_counts": {table: value["count"] for table, value in records.items()},
        "safety": {
            "qa_workspace_only": True,
            "self_service_verified": True,
            "customer_workspace_write": False,
            "database_write": False,
            "storage_write": False,
        },
    }
