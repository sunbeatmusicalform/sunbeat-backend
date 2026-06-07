from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from app.core.database import supabase

RELEASE_INTAKE_HISTORY_SOURCE = "submitter_history"
RELEASE_INTAKE_WORKFLOW_TYPE = "release_intake"
RELEASE_INTAKE_DRAFTS_TABLE = "release_intake_drafts"
SUBMISSIONS_TABLE = "submissions"

HISTORY_LOOKUP_MIN_QUERY_LENGTH = 2
HISTORY_LOOKUP_MAX_LIMIT = 10
HISTORY_LOOKUP_CANDIDATE_LIMIT = 100

RELEASE_INTAKE_HISTORY_FIELDS = frozenset(
    {
        "primary_artists",
        "featured_artists",
        "interpreters",
        "authors",
        "publishers",
        "phonographic_producer",
        "producers_musicians",
        "existing_profile_links",
        "cover_link",
        "presskit_link",
        "promo_assets_link",
    }
)

TRACK_HISTORY_FIELDS = frozenset(
    {
        "primary_artists",
        "featured_artists",
        "interpreters",
        "authors",
        "publishers",
        "phonographic_producer",
        "producers_musicians",
        "existing_profile_links",
    }
)

PROJECT_HISTORY_FIELDS = frozenset(
    {
        "cover_link",
        "presskit_link",
        "promo_assets_link",
    }
)

MULTI_VALUE_FIELDS = frozenset(
    {
        "primary_artists",
        "featured_artists",
        "interpreters",
        "authors",
        "publishers",
        "phonographic_producer",
        "producers_musicians",
    }
)

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
SLUG_TEXT_PATTERN = re.compile(r"[^a-z0-9_-]+")


@dataclass(frozen=True)
class ReleaseIntakeHistoryContext:
    workspace_slug: str
    submitter_email: str


def _empty_response() -> Dict[str, Any]:
    return {"ok": True, "items": []}


def _normalized_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text) if text else None


def _normalized_slug(value: Any) -> Optional[str]:
    text = _normalized_text(value)
    if not text:
        return None

    normalized = SLUG_TEXT_PATTERN.sub("_", text.lower()).strip("_")
    return normalized or None


def _normalized_email(value: Any) -> Optional[str]:
    text = _normalized_text(value)
    return text.lower() if text else None


def _normalized_lookup_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\s+", " ", text.strip().lower())
    return text.replace("%", "").replace("_", "").replace("\\", "")


def _bounded_lookup_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = HISTORY_LOOKUP_MAX_LIMIT

    if parsed < 1:
        return 1

    return min(parsed, HISTORY_LOOKUP_MAX_LIMIT)


def _result_rows(result: Any) -> List[Dict[str, Any]]:
    rows = getattr(result, "data", None)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _first_result_row(result: Any) -> Optional[Dict[str, Any]]:
    rows = _result_rows(result)
    return rows[0] if rows else None


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _resolve_context_from_draft_token(
    *,
    workspace_slug: str,
    draft_token: str,
) -> Optional[ReleaseIntakeHistoryContext]:
    row = _first_result_row(
        supabase.table(RELEASE_INTAKE_DRAFTS_TABLE)
        .select("draft_token, client_slug, submitter_email, meta")
        .eq("draft_token", draft_token)
        .limit(1)
        .execute()
    )
    if not row:
        return None

    row_workspace = _normalized_slug(row.get("client_slug"))
    if row_workspace != workspace_slug:
        return None

    meta = _coerce_dict(row.get("meta"))
    workflow_type = _normalized_slug(meta.get("workflow_type"))
    if workflow_type != RELEASE_INTAKE_WORKFLOW_TYPE:
        return None

    submitter_email = _normalized_email(row.get("submitter_email"))
    if not submitter_email:
        return None

    return ReleaseIntakeHistoryContext(
        workspace_slug=workspace_slug,
        submitter_email=submitter_email,
    )


def _release_intake_workflow_from_payload(payload: Dict[str, Any]) -> bool:
    workflow_type = _normalized_slug(payload.get("workflow_type") or RELEASE_INTAKE_WORKFLOW_TYPE)
    return workflow_type == RELEASE_INTAKE_WORKFLOW_TYPE


def _payload_submitter_email(payload: Dict[str, Any]) -> Optional[str]:
    identification = _coerce_dict(payload.get("identification"))
    return _normalized_email(identification.get("submitter_email"))


def _resolve_context_from_edit_token(
    *,
    workspace_slug: str,
    edit_token: str,
) -> Optional[ReleaseIntakeHistoryContext]:
    row = _first_result_row(
        supabase.table(SUBMISSIONS_TABLE)
        .select("id, client_slug, email, payload")
        .eq("edit_token", edit_token)
        .limit(1)
        .execute()
    )
    if not row:
        return None

    row_workspace = _normalized_slug(row.get("client_slug"))
    if row_workspace != workspace_slug:
        return None

    payload = _coerce_dict(row.get("payload"))
    if not _release_intake_workflow_from_payload(payload):
        return None

    submitter_email = _normalized_email(row.get("email")) or _payload_submitter_email(payload)
    if not submitter_email:
        return None

    return ReleaseIntakeHistoryContext(
        workspace_slug=workspace_slug,
        submitter_email=submitter_email,
    )


def _resolve_history_context(
    *,
    workspace_slug: str,
    draft_token: Optional[str],
    edit_token: Optional[str],
) -> Optional[ReleaseIntakeHistoryContext]:
    clean_draft_token = _normalized_text(draft_token)
    clean_edit_token = _normalized_text(edit_token)

    if bool(clean_draft_token) == bool(clean_edit_token):
        return None

    if clean_draft_token:
        return _resolve_context_from_draft_token(
            workspace_slug=workspace_slug,
            draft_token=clean_draft_token,
        )

    return _resolve_context_from_edit_token(
        workspace_slug=workspace_slug,
        edit_token=clean_edit_token or "",
    )


def _split_suggestion_values(value: Any, *, split_commas: bool) -> Iterable[str]:
    if value is None:
        return []

    if isinstance(value, list):
        values: List[str] = []
        for item in value:
            values.extend(_split_suggestion_values(item, split_commas=split_commas))
        return values

    if isinstance(value, dict):
        return []

    text = _normalized_text(value)
    if not text:
        return []

    if not split_commas:
        return [text]

    return [_normalized_text(part) or "" for part in text.split(",")]


def _is_safe_suggestion_value(value: str) -> bool:
    if not value:
        return False
    if len(value) > 500:
        return False
    if EMAIL_PATTERN.search(value):
        return False
    return True


def _extract_history_values(payload: Dict[str, Any], field: str) -> Iterable[str]:
    if field in PROJECT_HISTORY_FIELDS:
        project = _coerce_dict(payload.get("project"))
        return _split_suggestion_values(project.get(field), split_commas=False)

    if field in TRACK_HISTORY_FIELDS:
        values: List[str] = []
        for track in _coerce_list(payload.get("tracks")):
            track_payload = _coerce_dict(track)
            values.extend(
                _split_suggestion_values(
                    track_payload.get(field),
                    split_commas=field in MULTI_VALUE_FIELDS,
                )
            )
        return values

    return []


def _row_matches_history_context(
    row: Dict[str, Any],
    context: ReleaseIntakeHistoryContext,
) -> bool:
    if _normalized_slug(row.get("client_slug")) != context.workspace_slug:
        return False

    payload = _coerce_dict(row.get("payload"))
    if not _release_intake_workflow_from_payload(payload):
        return False

    row_email = _normalized_email(row.get("email")) or _payload_submitter_email(payload)
    return row_email == context.submitter_email


def _submission_last_used_at(row: Dict[str, Any]) -> str:
    return str(
        row.get("submitted_at")
        or row.get("updated_at")
        or row.get("created_at")
        or ""
    )


def _load_history_submission_rows(
    context: ReleaseIntakeHistoryContext,
) -> List[Dict[str, Any]]:
    result = (
        supabase.table(SUBMISSIONS_TABLE)
        .select("id, client_slug, email, created_at, updated_at, submitted_at, payload")
        .eq("client_slug", context.workspace_slug)
        .eq("email", context.submitter_email)
        .order("updated_at", desc=True)
        .limit(HISTORY_LOOKUP_CANDIDATE_LIMIT)
        .execute()
    )
    return _result_rows(result)


def lookup_release_intake_submitter_history(
    *,
    workspace_slug: str,
    field: str,
    query: str,
    limit: Optional[int] = None,
    draft_token: Optional[str] = None,
    edit_token: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_workspace = _normalized_slug(workspace_slug) or ""
    normalized_field = _normalized_slug(field) or ""
    normalized_query = _normalized_lookup_text(query)
    bounded_limit = _bounded_lookup_limit(limit)

    if (
        not normalized_workspace
        or normalized_field not in RELEASE_INTAKE_HISTORY_FIELDS
        or len(normalized_query) < HISTORY_LOOKUP_MIN_QUERY_LENGTH
    ):
        return _empty_response()

    context = _resolve_history_context(
        workspace_slug=normalized_workspace,
        draft_token=draft_token,
        edit_token=edit_token,
    )
    if not context:
        return _empty_response()

    collected: Dict[str, Dict[str, Any]] = {}
    for row in _load_history_submission_rows(context):
        if not _row_matches_history_context(row, context):
            continue

        last_used_at = _submission_last_used_at(row)
        payload = _coerce_dict(row.get("payload"))

        for raw_value in _extract_history_values(payload, normalized_field):
            value = _normalized_text(raw_value) or ""
            if not _is_safe_suggestion_value(value):
                continue
            if normalized_query not in _normalized_lookup_text(value):
                continue

            key = _normalized_lookup_text(value)
            existing = collected.get(key)
            if existing:
                existing["count"] += 1
                if last_used_at > str(existing.get("lastUsedAt") or ""):
                    existing["lastUsedAt"] = last_used_at
                continue

            collected[key] = {
                "value": value,
                "field": normalized_field,
                "source": RELEASE_INTAKE_HISTORY_SOURCE,
                "count": 1,
                "lastUsedAt": last_used_at,
            }

    items = sorted(
        collected.values(),
        key=lambda item: (
            int(item.get("count") or 0),
            str(item.get("lastUsedAt") or ""),
            _normalized_lookup_text(item.get("value")),
        ),
        reverse=True,
    )

    return {"ok": True, "items": items[:bounded_limit]}
