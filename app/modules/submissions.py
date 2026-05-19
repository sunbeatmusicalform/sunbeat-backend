from __future__ import annotations

import inspect
import logging
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import ValidationError

from app.core.config import settings
from app.core.database import supabase
from app.modules.workflow_registry import (
    DEFAULT_WORKFLOW_TYPE,
    RIGHTS_CLEARANCE_WORKFLOW_TYPE,
    build_frontend_workflow_path,
    resolve_workflow_identity,
)
from app.schemas.submission import (
    CompanyRegistrySubmissionPayload,
    ReleaseIntakeSubmissionPayload,
    RightsClearanceSubmissionPayload,
    WorkflowSubmissionPayload,
    validate_submission_payload,
)
from app.services.airtable import (
    upsert_airtable_project,
    upsert_airtable_tracks,
    update_airtable_project_focus_track,
)
from app.services.airtable_company_registry import sync_company_registry_to_airtable, update_company_registry_in_airtable
from app.services.airtable_rights_clearance import sync_rights_clearance_to_airtable, update_rights_clearance_case_in_airtable
from app.services.email import send_edit_link_email, send_submission_summary_email
from app.services.workspace_config import get_workflow_settings, get_email_event_config
from app.services.google_drive import sync_clearance_to_google_drive, sync_submission_to_google_drive

logger = logging.getLogger("sunbeat.submissions")

router = APIRouter(prefix="/submissions", tags=["Submissions"])

EMAIL_SETTINGS_STEP_KEY = "__workspace_settings__"
EMAIL_SETTINGS_FIELD_KEY = "submission_notification_emails"
IDEMPOTENCY_WINDOW = timedelta(minutes=10)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_uuid(value: str | UUID | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    return str(UUID(str(value)))


def _generate_edit_token() -> str:
    return secrets.token_urlsafe(24)


def _safe_model_dump(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    return dict(obj)


def _run_google_drive_sync_task(
    payload: WorkflowSubmissionPayload,
    submission_id: str,
) -> None:
    try:
        drive_result = sync_submission_to_google_drive(
            payload,
            submission_id=submission_id,
        )
    except Exception:
        logger.exception(
            "Google Drive background sync raised unexpectedly submission_id=%s",
            submission_id,
        )
        return

    status = str(drive_result.get("status") or "unknown")
    log_method = logger.warning if status in {"partial", "failed"} else logger.info
    log_method(
        "Google Drive background sync finished submission_id=%s status=%s result=%s",
        submission_id,
        status,
        drive_result,
    )


def _run_clearance_drive_sync_task(
    payload: WorkflowSubmissionPayload,
    submission_id: str,
) -> None:
    try:
        drive_result = sync_clearance_to_google_drive(payload)
    except Exception:
        logger.exception(
            "Clearance Google Drive background sync raised unexpectedly submission_id=%s",
            submission_id,
        )
        return

    status = str(drive_result.get("status") or "unknown")
    log_method = logger.warning if status in {"partial", "failed"} else logger.info
    log_method(
        "Clearance Google Drive background sync finished submission_id=%s status=%s result=%s",
        submission_id,
        status,
        drive_result,
    )


def _queue_google_drive_sync(
    background_tasks: BackgroundTasks,
    payload: WorkflowSubmissionPayload,
    submission_id: str,
) -> Dict[str, Any]:
    is_release_intake = _is_release_intake_payload(payload)
    is_clearance = not is_release_intake and isinstance(payload, RightsClearanceSubmissionPayload)

    if not is_release_intake and not is_clearance:
        return {"ok": True, "status": "skipped"}

    if not settings.GOOGLE_DRIVE_ENABLED:
        logger.info(
            "Google Drive sync skipped submission_id=%s reason=disabled",
            submission_id,
        )
        return {"ok": True, "status": "skipped"}

    _drive_wf_type = "release_intake" if is_release_intake else "rights_clearance"
    _drive_cfg = get_workflow_settings(payload.workspace_slug, _drive_wf_type)
    if not _drive_cfg.get("drive_sync_enabled", True):
        logger.info(
            "Google Drive sync skipped by workspace config submission_id=%s workspace=%s workflow=%s",
            submission_id,
            payload.workspace_slug,
            _drive_wf_type,
        )
        return {"ok": True, "status": "skipped_config"}

    drive_payload = (
        payload.model_copy(deep=True)
        if hasattr(payload, "model_copy")
        else validate_submission_payload(_safe_model_dump(payload))
    )

    task_fn = _run_clearance_drive_sync_task if is_clearance else _run_google_drive_sync_task
    try:
        background_tasks.add_task(
            task_fn,
            drive_payload,
            submission_id,
        )
    except Exception:
        logger.exception(
            "Failed to queue Google Drive background sync submission_id=%s",
            submission_id,
        )
        return {"ok": False, "status": "failed"}

    logger.info(
        "Google Drive background sync queued submission_id=%s workspace_slug=%s",
        submission_id,
        payload.workspace_slug,
    )
    return {"ok": True, "status": "partial"}


def _is_rights_clearance_payload(payload: WorkflowSubmissionPayload) -> bool:
    return isinstance(payload, RightsClearanceSubmissionPayload)


def _is_release_intake_payload(payload: WorkflowSubmissionPayload) -> bool:
    return isinstance(payload, ReleaseIntakeSubmissionPayload)


def _is_company_registry_payload(payload: WorkflowSubmissionPayload) -> bool:
    return isinstance(payload, CompanyRegistrySubmissionPayload)


def _submission_workflow_type(payload: WorkflowSubmissionPayload) -> str:
    return resolve_workflow_identity(
        workspace_slug=payload.workspace_slug,
        workflow_type=getattr(payload, "workflow_type", None),
        form_version=getattr(getattr(payload, "meta", None), "form_version", None),
    )["workflow_type"]


def _submission_form_version(payload: WorkflowSubmissionPayload) -> str:
    return resolve_workflow_identity(
        workspace_slug=payload.workspace_slug,
        workflow_type=getattr(payload, "workflow_type", None),
        form_version=getattr(getattr(payload, "meta", None), "form_version", None),
    )["form_version"]


def _get_focus_track_name(payload: WorkflowSubmissionPayload) -> Optional[str]:
    if _is_rights_clearance_payload(payload):
        if getattr(payload.request_type, "clearance_format", "") == "music_release_clearance_intake":
            first_track = (payload.tracks or [None])[0]
            if first_track and getattr(first_track, "title", None):
                return str(first_track.title).strip() or None

        clearance_scope = getattr(payload, "clearance_scope", None)
        if clearance_scope and getattr(clearance_scope, "music_title", None):
            return str(clearance_scope.music_title).strip() or None

        return None

    focus_track = _get_focus_track(payload)
    if focus_track and getattr(focus_track, "title", None):
        return focus_track.title

    marketing = payload.marketing

    if marketing and getattr(marketing, "focus_track_name", None):
        return marketing.focus_track_name

    return None


def _get_focus_track(payload: WorkflowSubmissionPayload) -> Any | None:
    if _is_rights_clearance_payload(payload):
        return None

    marketing = payload.marketing
    focus_track_name = str(
        getattr(marketing, "focus_track_name", "") or ""
    ).strip().lower()

    if focus_track_name:
        matching_track = next(
            (
                track
                for track in payload.tracks
                if str(getattr(track, "title", "") or "").strip().lower() == focus_track_name
            ),
            None,
        )
        if matching_track:
            return matching_track

    focus_track = next(
        (track for track in payload.tracks if getattr(track, "is_focus_track", False)),
        None,
    )
    if focus_track:
        return focus_track

    return payload.tracks[0] if payload.tracks else None


def _get_primary_artist(payload: WorkflowSubmissionPayload) -> Optional[str]:
    if _is_company_registry_payload(payload):
        return payload.company_data.fantasy_name or payload.company_data.legal_name or None
    if _is_rights_clearance_payload(payload):
        if getattr(payload.request_type, "clearance_format", "") == "music_release_clearance_intake":
            first_track = (payload.tracks or [None])[0]
            if first_track and getattr(first_track, "primary_artists", None):
                primary_artists = str(first_track.primary_artists).strip()
                if primary_artists:
                    return primary_artists

        clearance_scope = getattr(payload, "clearance_scope", None)
        artist_name = str(getattr(clearance_scope, "artist_name", "") or "").strip()
        if artist_name:
            return artist_name

        responsible_company = str(
            getattr(payload.project_context, "responsible_company", "") or ""
        ).strip()
        if responsible_company:
            return responsible_company

        requester_company = str(
            getattr(payload.requester_identification, "requester_company", "") or ""
        ).strip()
        return requester_company or None

    focus_track = _get_focus_track(payload)
    if focus_track:
        primary_artist = str(getattr(focus_track, "primary_artists", "") or "").strip()
        if primary_artist:
            return primary_artist

    first_track = payload.tracks[0] if payload.tracks else None
    if first_track:
        primary_artist = str(getattr(first_track, "primary_artists", "") or "").strip()
        if primary_artist:
            return primary_artist

    return None


def _normalize_release_date(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _parse_release_date(value: Any) -> Optional[date]:
    text = _normalize_release_date(value)
    if not text:
        return None

    candidates = [text]
    if "T" in text:
        candidates.append(text.split("T", 1)[0])
    if " " in text:
        candidates.append(text.split(" ", 1)[0])

    for candidate in candidates:
        normalized = candidate.strip()
        if not normalized:
            continue

        try:
            return date.fromisoformat(normalized)
        except ValueError:
            pass

        try:
            return datetime.fromisoformat(
                normalized.replace("Z", "+00:00")
            ).date()
        except ValueError:
            continue

    return None


def _calculate_days_until_release(release_date: Any) -> Optional[int]:
    parsed_release_date = _parse_release_date(release_date)
    if not parsed_release_date:
        return None

    today = datetime.now().date()
    return (parsed_release_date - today).days


def _get_submission_contact_email(payload: WorkflowSubmissionPayload) -> str:
    if _is_rights_clearance_payload(payload):
        return payload.requester_identification.requester_email
    if _is_company_registry_payload(payload):
        return str(payload.legal_representative.email)
    return payload.identification.submitter_email


def _get_submission_contact_name(payload: WorkflowSubmissionPayload) -> str:
    if _is_rights_clearance_payload(payload):
        return payload.requester_identification.requester_name
    if _is_company_registry_payload(payload):
        return payload.legal_representative.name
    return payload.identification.submitter_name


def _get_submission_project_title(payload: WorkflowSubmissionPayload) -> str:
    if _is_rights_clearance_payload(payload):
        return payload.project_context.project_title
    if _is_company_registry_payload(payload):
        return payload.company_data.fantasy_name or payload.company_data.legal_name
    return payload.identification.project_title


def _get_submission_release_date(payload: WorkflowSubmissionPayload) -> Optional[str]:
    if _is_rights_clearance_payload(payload):
        return _normalize_release_date(payload.project_context.release_or_start_date)
    if _is_company_registry_payload(payload):
        return None
    return _normalize_release_date(getattr(payload.project, "release_date", None))


def _build_post_submit_email_subject(
    *,
    project_title: Optional[str],
    release_date: Optional[str],
    primary_artist: Optional[str],
) -> str:
    safe_project_title = str(project_title or "").strip() or "Projeto sem titulo"
    safe_release_date = str(release_date or "").strip() or "data nao informada"
    safe_primary_artist = str(primary_artist or "").strip() or "artista nao informado"
    return (
        f"Resumo do lancamento - {safe_project_title} - "
        f"{safe_release_date} + {safe_primary_artist}"
    )


def _bool_from_yes_no(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"yes", "sim", "true", "1"}


def _yes_no_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"yes", "sim", "true", "1"}:
        return "Sim"
    if text in {"no", "nao", "false", "0"}:
        return "Nao"
    return str(value).strip()


def _build_edit_url(
    edit_token: str,
    workspace_slug: str,
    workflow_type: str = DEFAULT_WORKFLOW_TYPE,
) -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    path = build_frontend_workflow_path(
        workspace_slug=workspace_slug,
        workflow_type=workflow_type,
    )
    return f"{base}{path}?edit_token={edit_token}"


def _mark_draft_as_submitted(draft_token: str | None) -> None:
    if not draft_token:
        return

    try:
        supabase.table("release_intake_drafts").update(
            {
                "status": "submitted",
                "updated_at": _utc_now_iso(),
            }
        ).eq("draft_token", draft_token).execute()
    except Exception as exc:
        logger.warning("Could not mark release_intake_drafts as submitted: %s", exc)


def _update_submission_airtable_success(submission_id: str, airtable_project_id: str) -> None:
    supabase.table("submissions").update(
        {
            "airtable_project_id": airtable_project_id,
            "airtable_sync_status": "synced",
            "airtable_synced_at": _utc_now_iso(),
            "airtable_sync_error": None,
            "updated_at": _utc_now_iso(),
        }
    ).eq("id", submission_id).execute()


def _update_submission_airtable_failed(submission_id: str, error_message: str) -> None:
    supabase.table("submissions").update(
        {
            "airtable_sync_status": "failed",
            "airtable_sync_error": error_message[:1000],
            "updated_at": _utc_now_iso(),
        }
    ).eq("id", submission_id).execute()


def _update_submission_email_sent(submission_id: str) -> None:
    supabase.table("submissions").update(
        {
            "email_status": "sent",
            "email_sent_at": _utc_now_iso(),
            "email_error": None,
            "updated_at": _utc_now_iso(),
        }
    ).eq("id", submission_id).execute()


def _update_submission_email_failed(submission_id: str, error_message: str) -> None:
    supabase.table("submissions").update(
        {
            "email_status": "failed",
            "email_error": error_message[:1000],
            "updated_at": _utc_now_iso(),
        }
    ).eq("id", submission_id).execute()


def _update_submission_email_skipped(submission_id: str, reason: str) -> None:
    supabase.table("submissions").update(
        {
            "email_status": "skipped",
            "email_error": reason[:1000],
            "updated_at": _utc_now_iso(),
        }
    ).eq("id", submission_id).execute()


def _load_submission_row(submission_id: str) -> Dict[str, Any] | None:
    result = (
        supabase.table("submissions")
        .select("*")
        .eq("id", submission_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _load_submission_by_edit_token(edit_token: str) -> Dict[str, Any] | None:
    result = (
        supabase.table("submissions")
        .select("*")
        .eq("edit_token", edit_token)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _load_submission_tracks(submission_id: str) -> List[Dict[str, Any]]:
    result = (
        supabase.table("tracks")
        .select("*")
        .eq("submission_id", submission_id)
        .execute()
    )
    rows = getattr(result, "data", None) or []
    return sorted(
        rows,
        key=lambda row: (
            int(row.get("order_number") or 0),
            str(row.get("created_at") or ""),
            str(row.get("id") or ""),
        ),
    )


def _clean_idempotency_key(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _is_within_idempotency_window(
    value: Any,
    *,
    reference: datetime,
) -> bool:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return False
    return reference - parsed <= IDEMPOTENCY_WINDOW


def _load_recent_idempotent_submission(
    *,
    idempotency_key: str | None,
    reference: datetime,
    submission_id: str | None = None,
    edit_token: str | None = None,
    draft_token: str | None = None,
) -> Dict[str, Any] | None:
    clean_key = _clean_idempotency_key(idempotency_key)
    if not clean_key:
        return None

    result = (
        supabase.table("submissions")
        .select("*")
        .eq("idempotency_key", clean_key)
        .execute()
    )

    rows = getattr(result, "data", None) or []
    rows = sorted(
        rows,
        key=lambda row: (
            _parse_iso_datetime(row.get("updated_at") or row.get("created_at"))
            or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )

    for row in rows:
        if submission_id and row.get("id") != submission_id:
            continue
        if edit_token and row.get("edit_token") != edit_token:
            continue
        if draft_token and _as_uuid(row.get("draft_token")) != _as_uuid(draft_token):
            continue
        if _is_within_idempotency_window(
            row.get("updated_at") or row.get("created_at"),
            reference=reference,
        ):
            return row

    return None


def _count_active_tracks(track_rows: List[Dict[str, Any]]) -> int:
    return sum(1 for row in track_rows if not row.get("deleted_at"))


def _response_notification_email_status(row: Dict[str, Any]) -> str:
    if row.get("summary_email_sent"):
        return "already_sent"
    return "skipped"


def _build_submission_replay_response(
    row: Dict[str, Any],
    *,
    message: str,
) -> Dict[str, Any]:
    workflow_identity = _workflow_identity_from_row(row)
    track_rows = _load_submission_tracks(str(row.get("id") or ""))

    response: Dict[str, Any] = {
        "ok": True,
        "submission_id": row.get("id"),
        "draft_token": row.get("draft_token"),
        "edit_token": row.get("edit_token"),
        "tracks_created": _count_active_tracks(track_rows),
        "message": message,
        "workflow": {
            "workspace_slug": workflow_identity["workspace_slug"],
            "workflow_type": workflow_identity["workflow_type"],
            "form_version": workflow_identity["form_version"],
        },
        "sync": {
            "supabase": "ok",
            "airtable": row.get("airtable_sync_status") or "pending",
            "email": row.get("email_status") or "pending",
            "notification_email": _response_notification_email_status(row),
        },
        "replayed": True,
    }

    if row.get("airtable_project_id"):
        response["airtable_project_id"] = row.get("airtable_project_id")

    if row.get("summary_email_message_id"):
        response["notification_email_message_id"] = row.get(
            "summary_email_message_id"
        )

    return response


def _workflow_identity_from_row(row: Dict[str, Any]) -> Dict[str, str]:
    payload = _coerce_dict(row.get("payload"))
    meta = _coerce_dict(payload.get("meta"))
    return resolve_workflow_identity(
        workspace_slug=payload.get("workspace_slug") or row.get("client_slug") or "atabaque",
        workflow_type=payload.get("workflow_type"),
        form_version=meta.get("form_version"),
    )


def _build_payload_dump(payload: WorkflowSubmissionPayload) -> Dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return _safe_model_dump(payload)


def _build_release_track_payloads(
    payload: ReleaseIntakeSubmissionPayload,
    *,
    generate_client_track_ids: bool = True,
) -> List[Dict[str, Any]]:
    track_payloads: List[Dict[str, Any]] = []

    for index, track in enumerate(payload.tracks, start=1):
        track_payload = _safe_model_dump(track)
        track_payload["order_number"] = track_payload.get("order_number") or index
        if generate_client_track_ids and not str(
            track_payload.get("client_track_id") or ""
        ).strip():
            track_payload["client_track_id"] = str(uuid4())
        if not str(track_payload.get("local_id") or "").strip():
            track_payload["local_id"] = track_payload.get("client_track_id") or str(
                uuid4()
            )
        track_payloads.append(track_payload)

    return track_payloads


def _build_release_payload_dump(
    payload: ReleaseIntakeSubmissionPayload,
    track_payloads: List[Dict[str, Any]],
) -> Dict[str, Any]:
    payload_dump = _build_payload_dump(payload)
    payload_dump["tracks"] = track_payloads
    return payload_dump


def _build_release_track_row(
    *,
    submission_id: str,
    draft_token: str | None,
    now_iso: str,
    track_payload: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "submission_id": submission_id,
        "draft_token": draft_token,
        "client_track_id": track_payload.get("client_track_id"),
        "order_number": track_payload.get("order_number"),
        "title": track_payload.get("title"),
        "artists": track_payload.get("primary_artists"),
        "authors": track_payload.get("authors"),
        "lyrics": track_payload.get("lyrics"),
        "explicit": _bool_from_yes_no(track_payload.get("explicit_content")),
        "deleted_at": None,
        "created_at": now_iso,
    }


def _build_submission_update_row(
    *,
    existing_row: Dict[str, Any],
    payload: ReleaseIntakeSubmissionPayload,
    payload_dump: Dict[str, Any],
    now_iso: str,
    idempotency_key: str | None,
) -> Dict[str, Any]:
    identification = payload.identification
    project = payload.project
    marketing = payload.marketing

    return {
        "updated_at": now_iso,
        "version": int(existing_row.get("version") or 1) + 1,
        "is_update": True,
        "client_slug": payload.workspace_slug,
        "email": identification.submitter_email,
        "artist_name": identification.submitter_name,
        "release_type": identification.release_type,
        "release_title": identification.project_title,
        "main_title": identification.project_title,
        "track_title": _get_focus_track_name(payload),
        "genre": project.genre,
        "release_date": project.release_date,
        "cover_url": getattr(getattr(project, "cover_file", None), "public_url", None)
        or getattr(project, "cover_link", None),
        "cover_path": getattr(getattr(project, "cover_file", None), "storage_path", None),
        "marketing_json": payload_dump.get("marketing") or _safe_model_dump(marketing),
        "tracks_json": payload_dump.get("tracks") or [],
        "payload": payload_dump,
        "idempotency_key": idempotency_key,
    }


def _insert_submission_revision(
    *,
    submission_id: str,
    version: int,
    payload: Dict[str, Any],
) -> None:
    supabase.table("submissions_revisions").insert(
        {
            "id": str(uuid4()),
            "submission_id": submission_id,
            "version": version,
            "payload": payload,
        }
    ).execute()


def _sorted_track_rows(track_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        track_rows,
        key=lambda row: (
            bool(row.get("deleted_at")),
            int(row.get("order_number") or 0),
            str(row.get("created_at") or ""),
            str(row.get("id") or ""),
        ),
    )


def _normalize_track_match_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _ensure_track_rows_have_client_track_ids(
    track_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    for row in track_rows:
        if str(row.get("client_track_id") or "").strip():
            continue

        client_track_id = str(uuid4())
        supabase.table("tracks").update(
            {
                "client_track_id": client_track_id,
            }
        ).eq("id", row["id"]).execute()
        row["client_track_id"] = client_track_id

    return track_rows


def _match_existing_track_row(
    track_payload: Dict[str, Any],
    track_rows: List[Dict[str, Any]],
    *,
    used_client_track_ids: set[str],
) -> Dict[str, Any] | None:
    title = _normalize_track_match_text(track_payload.get("title"))
    order_number = int(track_payload.get("order_number") or 0)

    def _is_available(row: Dict[str, Any]) -> bool:
        client_track_id = str(row.get("client_track_id") or "").strip()
        return bool(client_track_id and client_track_id not in used_client_track_ids)

    matchers = [
        lambda row: _is_available(row)
        and int(row.get("order_number") or 0) == order_number
        and _normalize_track_match_text(row.get("title")) == title,
        lambda row: _is_available(row)
        and _normalize_track_match_text(row.get("title")) == title,
        lambda row: _is_available(row)
        and int(row.get("order_number") or 0) == order_number,
    ]

    for matcher in matchers:
        for row in track_rows:
            if matcher(row):
                return row

    return None


def _prepare_release_track_payloads_for_update(
    payload: ReleaseIntakeSubmissionPayload,
    existing_track_rows: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    hydrated_track_rows = _ensure_track_rows_have_client_track_ids(existing_track_rows)
    candidate_rows = _sorted_track_rows(hydrated_track_rows)
    existing_client_track_ids = {
        str(row.get("client_track_id") or "").strip()
        for row in hydrated_track_rows
        if str(row.get("client_track_id") or "").strip()
    }
    used_client_track_ids: set[str] = set()
    prepared_track_payloads: List[Dict[str, Any]] = []

    for track_payload in _build_release_track_payloads(
        payload,
        generate_client_track_ids=False,
    ):
        client_track_id = str(track_payload.get("client_track_id") or "").strip()
        if not client_track_id:
            matched_row = _match_existing_track_row(
                track_payload,
                candidate_rows,
                used_client_track_ids=used_client_track_ids,
            )
            if matched_row:
                client_track_id = str(matched_row["client_track_id"])
            else:
                client_track_id = str(uuid4())

        track_payload["client_track_id"] = client_track_id
        if not str(track_payload.get("local_id") or "").strip():
            track_payload["local_id"] = client_track_id

        if client_track_id in existing_client_track_ids:
            used_client_track_ids.add(client_track_id)

        prepared_track_payloads.append(track_payload)

    return prepared_track_payloads, hydrated_track_rows


def _build_release_track_update_row(
    *,
    draft_token: str | None,
    track_payload: Dict[str, Any],
    deleted_at: str | None = None,
) -> Dict[str, Any]:
    return {
        "draft_token": draft_token,
        "client_track_id": track_payload.get("client_track_id"),
        "order_number": track_payload.get("order_number"),
        "title": track_payload.get("title"),
        "artists": track_payload.get("primary_artists"),
        "authors": track_payload.get("authors"),
        "lyrics": track_payload.get("lyrics"),
        "explicit": _bool_from_yes_no(track_payload.get("explicit_content")),
        "deleted_at": deleted_at,
    }


def _reconcile_release_tracks(
    *,
    submission_id: str,
    draft_token: str | None,
    track_payloads: List[Dict[str, Any]],
    existing_track_rows: List[Dict[str, Any]],
    now_iso: str,
) -> Dict[str, Any]:
    hydrated_track_rows = _ensure_track_rows_have_client_track_ids(existing_track_rows)
    existing_by_client_track_id = {
        str(row.get("client_track_id") or ""): row
        for row in hydrated_track_rows
        if str(row.get("client_track_id") or "").strip()
    }
    seen_client_track_ids: set[str] = set()
    inserted_tracks: List[Dict[str, Any]] = []

    for track_payload in track_payloads:
        client_track_id = str(track_payload.get("client_track_id") or "").strip()
        if not client_track_id:
            client_track_id = str(uuid4())
            track_payload["client_track_id"] = client_track_id

        seen_client_track_ids.add(client_track_id)
        existing_row = existing_by_client_track_id.get(client_track_id)

        if existing_row:
            update_row = _build_release_track_update_row(
                draft_token=draft_token,
                track_payload=track_payload,
                deleted_at=None,
            )
            supabase.table("tracks").update(update_row).eq("id", existing_row["id"]).execute()
            existing_row.update(update_row)
            continue

        insert_row = _build_release_track_row(
            submission_id=submission_id,
            draft_token=draft_token,
            now_iso=now_iso,
            track_payload=track_payload,
        )
        insert_result = supabase.table("tracks").insert(insert_row).execute()
        created_rows = getattr(insert_result, "data", None) or [insert_row]
        created_row = created_rows[0]
        inserted_tracks.append(created_row)
        existing_by_client_track_id[client_track_id] = created_row

    for existing_row in hydrated_track_rows:
        client_track_id = str(existing_row.get("client_track_id") or "").strip()
        if not client_track_id or client_track_id in seen_client_track_ids:
            continue
        if existing_row.get("deleted_at"):
            continue

        supabase.table("tracks").update(
            {
                "deleted_at": now_iso,
            }
        ).eq("id", existing_row["id"]).execute()
        existing_row["deleted_at"] = now_iso

    return {
        "inserted_tracks": inserted_tracks,
        "active_tracks_count": len(track_payloads),
    }


def _apply_track_client_ids_to_payload_tracks(
    track_payloads: List[Dict[str, Any]],
    track_rows: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], bool]:
    active_track_rows = [
        row for row in _sorted_track_rows(track_rows) if not row.get("deleted_at")
    ]
    rows_by_client_track_id = {
        str(row.get("client_track_id") or "").strip(): row
        for row in active_track_rows
        if str(row.get("client_track_id") or "").strip()
    }
    used_row_ids: set[str] = set()
    changed = False
    normalized_tracks: List[Dict[str, Any]] = []

    for track_payload in track_payloads:
        normalized_track = dict(track_payload)
        client_track_id = str(normalized_track.get("client_track_id") or "").strip()
        matched_row: Dict[str, Any] | None = None

        if client_track_id:
            matched_row = rows_by_client_track_id.get(client_track_id)
        else:
            matched_row = _match_existing_track_row(
                normalized_track,
                active_track_rows,
                used_client_track_ids=used_row_ids,
            )
            if matched_row:
                client_track_id = str(matched_row["client_track_id"])
                normalized_track["client_track_id"] = client_track_id
                changed = True

        if matched_row and not str(normalized_track.get("local_id") or "").strip():
            normalized_track["local_id"] = client_track_id
            changed = True

        if matched_row:
            used_row_ids.add(str(matched_row["client_track_id"]))

        normalized_tracks.append(normalized_track)

    return normalized_tracks, changed


def _persist_release_track_ids_on_submission(
    row: Dict[str, Any],
    track_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    payload = _coerce_dict(row.get("payload"))
    payload_tracks = _coerce_list(payload.get("tracks")) or _coerce_list(row.get("tracks_json"))
    if not payload_tracks:
        return row

    normalized_tracks, changed = _apply_track_client_ids_to_payload_tracks(
        payload_tracks,
        track_rows,
    )
    if not changed:
        return row

    update_row: Dict[str, Any] = {
        "tracks_json": normalized_tracks,
    }

    if payload:
        payload["tracks"] = normalized_tracks
        update_row["payload"] = payload

    update_result = (
        supabase.table("submissions")
        .update(update_row)
        .eq("id", row["id"])
        .execute()
    )
    updated_rows = getattr(update_result, "data", None) or []
    if updated_rows:
        updated_row = dict(row)
        updated_row.update(updated_rows[0])
        return updated_row

    updated_row = dict(row)
    updated_row.update(update_row)
    return updated_row


def _with_release_track_client_ids(
    data: Dict[str, Any],
    track_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if data.get("workflow_type") == RIGHTS_CLEARANCE_WORKFLOW_TYPE:
        return data

    normalized_tracks, _changed = _apply_track_client_ids_to_payload_tracks(
        _coerce_list(data.get("tracks")),
        track_rows,
    )
    updated_data = dict(data)
    updated_data["tracks"] = normalized_tracks
    return updated_data


def _build_updated_submission_response(
    *,
    submission_row: Dict[str, Any],
    payload: ReleaseIntakeSubmissionPayload,
    tracks_created: int,
) -> Dict[str, Any]:
    return {
        "ok": True,
        "submission_id": submission_row.get("id"),
        "draft_token": submission_row.get("draft_token"),
        "edit_token": submission_row.get("edit_token"),
        "tracks_created": tracks_created,
        "message": "Submission updated successfully.",
        "workflow": {
            "workspace_slug": payload.workspace_slug,
            "workflow_type": _submission_workflow_type(payload),
            "form_version": _submission_form_version(payload),
        },
        "sync": {
            "supabase": "ok",
            "airtable": submission_row.get("airtable_sync_status") or "pending",
            "email": "skipped",
            "notification_email": _response_notification_email_status(submission_row),
        },
    }


def _persist_airtable_track_ids(
    *,
    created_tracks: List[Dict[str, Any]],
    airtable_tracks: List[Dict[str, Any]],
) -> None:
    if not created_tracks or not airtable_tracks:
        return

    by_order_number: Dict[Any, str] = {}
    for item in airtable_tracks:
        fields = item.get("fields", {})
        order_number = fields.get("Ordem da Faixa")
        if order_number is None:
            order_number = fields.get("Track Order")
        if order_number is not None:
            by_order_number[order_number] = item["id"]

    for track in created_tracks:
        order_number = track.get("order_number")
        airtable_track_id = by_order_number.get(order_number)
        if not airtable_track_id:
            continue

        supabase.table("tracks").update(
            {
                "airtable_track_id": airtable_track_id,
            }
        ).eq("id", track["id"]).execute()


def _build_submission_row(
    *,
    payload: WorkflowSubmissionPayload,
    submission_id: str,
    edit_token: str,
    now_iso: str,
    prepared_track_payloads: Optional[List[Dict[str, Any]]] = None,
    idempotency_key: str | None = None,
) -> Dict[str, Any]:
    if _is_rights_clearance_payload(payload):
        requester = payload.requester_identification
        request_type = payload.request_type
        project_context = payload.project_context
        assets_references = payload.assets_references
        clearance_scope = payload.clearance_scope
        tracks = payload.tracks or []
        clearance_format = getattr(request_type, "clearance_format", "")
        first_track = tracks[0] if tracks else None
        track_title = (
            str(getattr(first_track, "title", "") or "").strip()
            if clearance_format == "music_release_clearance_intake"
            else str(getattr(clearance_scope, "music_title", "") or "").strip()
        ) or None

        return {
            "id": submission_id,
            "draft_token": _as_uuid(payload.draft_token),
            "status": "submitted",
            "created_at": now_iso,
            "updated_at": now_iso,
            "submitted_at": now_iso,
            "version": 1,
            "is_update": False,
            "edit_token": edit_token,
            "client_slug": payload.workspace_slug,
            "email": requester.requester_email,
            "artist_name": requester.requester_name,
            "release_type": RIGHTS_CLEARANCE_WORKFLOW_TYPE,
            "release_title": project_context.project_title,
            "main_title": project_context.project_title,
            "track_title": track_title,
            "genre": project_context.client_or_distributor,
            "release_date": project_context.release_or_start_date,
            "cover_url": None,
            "cover_path": None,
            "marketing_json": {
                "request_type": _safe_model_dump(request_type),
                "project_context": _safe_model_dump(project_context),
                "clearance_scope": _safe_model_dump(clearance_scope),
                "assets_references": _safe_model_dump(assets_references),
            },
            "tracks_json": [_safe_model_dump(track) for track in tracks],
            "payload": payload.model_dump() if hasattr(payload, "model_dump") else {},
            "airtable_sync_status": "skipped",
            "email_status": "pending",
            "idempotency_key": idempotency_key,
        }

    if _is_company_registry_payload(payload):
        company = payload.company_data
        legal = payload.legal_representative
        return {
            "id": submission_id,
            "draft_token": _as_uuid(payload.draft_token),
            "status": "submitted",
            "created_at": now_iso,
            "updated_at": now_iso,
            "submitted_at": now_iso,
            "version": 1,
            "is_update": False,
            "edit_token": edit_token,
            "client_slug": payload.workspace_slug,
            "email": str(legal.email),
            "artist_name": legal.name,
            "release_type": "company_registry",
            "release_title": company.fantasy_name or company.legal_name,
            "main_title": company.legal_name,
            "track_title": company.document_number,
            "genre": company.document_type,
            "release_date": None,
            "cover_url": None,
            "cover_path": None,
            "marketing_json": {
                "contract_representative": _safe_model_dump(payload.contract_representative),
                "financial_representative": _safe_model_dump(payload.financial_representative),
                "banking_data": _safe_model_dump(payload.banking_data),
            },
            "tracks_json": [],
            "payload": payload.model_dump() if hasattr(payload, "model_dump") else {},
            "airtable_sync_status": "pending",
            "email_status": "skipped",
            "idempotency_key": idempotency_key,
        }

    identification = payload.identification
    project = payload.project
    marketing = payload.marketing

    focus_track_name = _get_focus_track_name(payload)

    return {
        "id": submission_id,
        "draft_token": _as_uuid(payload.draft_token),
        "status": "submitted",
        "created_at": now_iso,
        "updated_at": now_iso,
        "submitted_at": now_iso,
        "version": 1,
        "is_update": False,
        "edit_token": edit_token,
        "client_slug": payload.workspace_slug,
        "email": identification.submitter_email,
        "artist_name": identification.submitter_name,
        "release_type": identification.release_type,
        "release_title": identification.project_title,
        "main_title": identification.project_title,
        "track_title": focus_track_name,
        "genre": project.genre,
        "release_date": project.release_date,
        "cover_url": getattr(getattr(project, "cover_file", None), "public_url", None)
        or getattr(project, "cover_link", None),
        "cover_path": getattr(getattr(project, "cover_file", None), "storage_path", None),
        "marketing_json": (
            _build_release_payload_dump(
                payload,
                prepared_track_payloads or _build_release_track_payloads(payload),
            ).get("marketing")
            or _safe_model_dump(marketing)
        ),
        "tracks_json": prepared_track_payloads or _build_release_track_payloads(payload),
        "payload": _build_release_payload_dump(
            payload,
            prepared_track_payloads or _build_release_track_payloads(payload),
        ),
        "airtable_sync_status": "pending",
        "email_status": "pending",
        "idempotency_key": idempotency_key,
    }


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    return []


def _parse_json_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        return value


def _coerce_dict(value: Any) -> Dict[str, Any]:
    parsed = _parse_json_string(value)
    if isinstance(parsed, dict):
        return parsed
    return {}


def _coerce_list(value: Any) -> List[Any]:
    parsed = _parse_json_string(value)
    if isinstance(parsed, list):
        return parsed
    return []


def _strip_empty_values(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            normalized = _strip_empty_values(item)
            if normalized in (None, "", [], {}):
                continue
            cleaned[key] = normalized
        return cleaned

    if isinstance(value, list):
        cleaned_list = [
            normalized
            for item in value
            for normalized in [_strip_empty_values(item)]
            if normalized not in (None, "", [], {})
        ]
        return cleaned_list

    if isinstance(value, str):
        normalized_text = value.strip()
        return normalized_text or None

    return value


def _has_meaningful_values(value: Any) -> bool:
    normalized = _strip_empty_values(value)
    return normalized not in (None, "", [], {})


def _infer_rights_clearance_format(
    clearance_scope: Dict[str, Any],
    tracks: Optional[List[Dict[str, Any]]] = None,
    project_context: Optional[Dict[str, Any]] = None,
) -> str:
    if tracks:
        return "music_release_clearance_intake"

    if str((project_context or {}).get("release_type") or "").strip():
        return "music_release_clearance_intake"

    audiovisual_keys = (
        "audiovisual_type",
        "director_name",
        "product_or_campaign_name",
        "scene_description",
        "sync_duration",
        "media_channels",
        "duration_sync",
    )
    music_keys = (
        "composer_author_info",
        "publisher_info",
        "material_type",
        "intended_use",
        "exclusivity",
    )

    if any(str(clearance_scope.get(key) or "").strip() for key in audiovisual_keys):
        return "audiovisual_product_sync"
    if any(str(clearance_scope.get(key) or "").strip() for key in music_keys):
        return "music_project_track"
    return ""


def _normalize_rights_requester_identification(
    value: Dict[str, Any],
    row: Dict[str, Any],
) -> Dict[str, Any]:
    return _strip_empty_values(
        {
            "requester_name": value.get("requester_name") or row.get("artist_name") or "",
            "requester_email": value.get("requester_email") or row.get("email") or "",
            "requester_company": value.get("requester_company")
            or value.get("responsible_company")
            or "",
            "requester_role": value.get("requester_role") or "",
        }
    ) or {}


def _normalize_rights_project_context(
    value: Dict[str, Any],
    row: Dict[str, Any],
) -> Dict[str, Any]:
    return _strip_empty_values(
        {
            "project_title": value.get("project_title")
            or row.get("release_title")
            or row.get("main_title")
            or "",
            "responsible_company": value.get("responsible_company") or "",
            "client_or_distributor": value.get("client_or_distributor")
            or value.get("distributor")
            or row.get("genre")
            or "",
            "release_or_start_date": value.get("release_or_start_date")
            or value.get("release_start_date")
            or row.get("release_date")
            or "",
            "release_type": value.get("release_type") or "",
            "project_synopsis": value.get("project_synopsis") or "",
            "has_brand_association": value.get("has_brand_association")
            or value.get("is_associated_with_brand")
            or "",
            "brand_context": value.get("brand_context")
            or value.get("brand_name_or_context")
            or "",
            "general_clearance_notes": value.get("general_clearance_notes")
            or value.get("general_notes")
            or "",
        }
    ) or {}


def _normalize_rights_clearance_scope(
    value: Dict[str, Any],
    row: Dict[str, Any],
) -> Dict[str, Any]:
    return _strip_empty_values(
        {
            "music_title": value.get("music_title") or row.get("track_title") or "",
            "artist_name": value.get("artist_name") or "",
            "phonogram_owner": value.get("phonogram_owner") or "",
            "territory": value.get("territory") or "",
            "licensing_period": value.get("licensing_period") or "",
            "composer_author_info": value.get("composer_author_info") or "",
            "publisher_info": value.get("publisher_info") or "",
            "material_type": value.get("material_type")
            or value.get("licensed_materials")
            or "",
            "intended_use": value.get("intended_use") or "",
            "exclusivity": value.get("exclusivity") or "",
            "audiovisual_type": value.get("audiovisual_type") or "",
            "director_name": value.get("director_name") or "",
            "product_or_campaign_name": value.get("product_or_campaign_name") or "",
            "scene_description": value.get("scene_description") or "",
            "sync_duration": value.get("sync_duration")
            or value.get("duration_sync")
            or "",
            "media_channels": value.get("media_channels") or "",
        }
    ) or {}


def _normalize_rights_assets_references(value: Dict[str, Any]) -> Dict[str, Any]:
    return _strip_empty_values(
        {
            "supporting_files": value.get("supporting_files")
            or value.get("additional_files")
            or [],
            "reference_links": value.get("reference_links") or "",
            "additional_notes": value.get("additional_notes")
            or value.get("supporting_notes")
            or "",
        }
    ) or {}


def _normalize_rights_tracks(value: Any) -> List[Dict[str, Any]]:
    tracks = _coerce_list(value)
    normalized_tracks: List[Dict[str, Any]] = []

    for index, item in enumerate(tracks, start=1):
        track = _coerce_dict(item)
        if not track:
            continue

        normalized_tracks.append(
            {
                "local_id": track.get("local_id") or f"rights-track-{index}",
                "order_number": track.get("order_number") or index,
                "title": track.get("title") or "",
                "primary_artists": track.get("primary_artists")
                or track.get("artists")
                or "",
                "authors": track.get("authors") or "",
                "publishers": track.get("publishers") or "",
                "phonogram_owner": track.get("phonogram_owner")
                or track.get("phonographic_producer")
                or "",
                "has_isrc": track.get("has_isrc") or "",
                "isrc_code": track.get("isrc_code") or track.get("isrc") or "",
                "notes_for_clearance": track.get("notes_for_clearance")
                or track.get("lyrics")
                or "",
            }
        )

    return _strip_empty_values(normalized_tracks) or []


def _normalize_edit_submission_data(row: Dict[str, Any]) -> Dict[str, Any]:
    raw_payload = row.get("payload")
    raw_marketing_json = row.get("marketing_json")
    raw_tracks_json = row.get("tracks_json")

    payload = _coerce_dict(raw_payload)
    meta = _coerce_dict(payload.get("meta"))
    workspace_slug = payload.get("workspace_slug") or row.get("client_slug") or "atabaque"
    workflow_identity = resolve_workflow_identity(
        workspace_slug=workspace_slug,
        workflow_type=payload.get("workflow_type"),
        form_version=meta.get("form_version"),
    )

    if workflow_identity["workflow_type"] == RIGHTS_CLEARANCE_WORKFLOW_TYPE:
        marketing_bundle = _coerce_dict(raw_marketing_json)
        requester_identification = _normalize_rights_requester_identification(
            _coerce_dict(payload.get("requester_identification")),
            row,
        )
        project_context = _normalize_rights_project_context(
            _coerce_dict(payload.get("project_context"))
            or _coerce_dict(marketing_bundle.get("project_context")),
            row,
        )
        tracks = _normalize_rights_tracks(
            payload.get("tracks") if payload.get("tracks") else raw_tracks_json
        )
        clearance_scope = _normalize_rights_clearance_scope(
            _coerce_dict(payload.get("clearance_scope"))
            or _coerce_dict(marketing_bundle.get("clearance_scope")),
            row,
        )
        assets_references = _normalize_rights_assets_references(
            _coerce_dict(payload.get("assets_references"))
            or _coerce_dict(marketing_bundle.get("assets_references"))
        )
        request_type = _strip_empty_values(
            _coerce_dict(payload.get("request_type"))
            or _coerce_dict(marketing_bundle.get("request_type"))
            or {
                "clearance_format": _infer_rights_clearance_format(
                    clearance_scope,
                    tracks,
                    project_context,
                ),
            }
        ) or {}

        debug = {
            "has_payload": bool(payload),
            "has_marketing_json": bool(raw_marketing_json),
            "has_tracks_json": bool(raw_tracks_json),
            "payload_is_string": isinstance(raw_payload, str),
            "marketing_json_is_string": isinstance(raw_marketing_json, str),
            "tracks_json_is_string": isinstance(raw_tracks_json, str),
            "payload_type": type(raw_payload).__name__,
            "marketing_json_type": type(raw_marketing_json).__name__,
            "tracks_json_type": type(raw_tracks_json).__name__,
            "clearance_format": request_type.get("clearance_format") or "",
            "has_requester_identification_data": _has_meaningful_values(
                requester_identification
            ),
            "has_request_type_data": _has_meaningful_values(request_type),
            "has_project_context_data": _has_meaningful_values(project_context),
            "has_tracks_data": len(tracks) > 0,
            "has_clearance_scope_data": _has_meaningful_values(clearance_scope),
            "has_assets_references_data": _has_meaningful_values(assets_references),
            "normalized_tracks_count": len(tracks),
            "normalized_release_date": project_context.get("release_or_start_date") or "",
            "hydration_ready": bool(
                _has_meaningful_values(requester_identification)
                or _has_meaningful_values(request_type)
                or _has_meaningful_values(project_context)
                or len(tracks) > 0
                or _has_meaningful_values(clearance_scope)
                or _has_meaningful_values(assets_references)
            ),
            "shape_source": "payload" if payload else "row_fallback",
        }

        return {
            "submission_id": row.get("id"),
            "draft_token": row.get("draft_token"),
            "edit_token": row.get("edit_token"),
            "workspace_slug": workflow_identity["workspace_slug"],
            "workflow_type": workflow_identity["workflow_type"],
            "requester_identification": requester_identification,
            "request_type": request_type,
            "project_context": project_context,
            "tracks": tracks,
            "clearance_scope": clearance_scope,
            "assets_references": assets_references,
            "meta": {
                "form_version": workflow_identity["form_version"],
                "source": meta.get("source") or workflow_identity["source"],
                "submitted_at": meta.get("submitted_at") or row.get("submitted_at"),
            },
            "debug": debug,
        }

    if workflow_identity["workflow_type"] == "company_registry":
        return {
            "submission_id": row.get("id"),
            "draft_token": row.get("draft_token"),
            "edit_token": row.get("edit_token"),
            "workspace_slug": workflow_identity["workspace_slug"],
            "workflow_type": workflow_identity["workflow_type"],
            "company_data": _coerce_dict(payload.get("company_data")) or {},
            "legal_representative": _coerce_dict(payload.get("legal_representative")) or {},
            "contract_representative": _coerce_dict(payload.get("contract_representative")) or {},
            "financial_representative": _coerce_dict(payload.get("financial_representative")) or {},
            "banking_data": _coerce_dict(payload.get("banking_data")) or {},
            "meta": {
                "form_version": workflow_identity["form_version"],
                "source": meta.get("source") or workflow_identity["source"],
                "submitted_at": meta.get("submitted_at") or row.get("submitted_at"),
            },
        }

    identification = _coerce_dict(payload.get("identification"))
    if not identification:
        identification = _strip_empty_values({
            "submitter_name": row.get("artist_name") or "",
            "submitter_email": row.get("email") or "",
            "project_title": row.get("release_title") or row.get("main_title") or "",
            "release_type": row.get("release_type") or "",
        }) or {}
    else:
        identification = _strip_empty_values(identification) or {}

    project = _coerce_dict(payload.get("project"))
    if not project:
        project = _strip_empty_values({
            "release_date": row.get("release_date") or "",
            "genre": row.get("genre") or "",
            "cover_link": row.get("cover_url") or "",
            "cover_file": {
                "storage_path": row.get("cover_path") or "",
                "public_url": row.get("cover_url") or "",
            }
            if row.get("cover_url") or row.get("cover_path")
            else None,
        }) or {}
    else:
        project = _strip_empty_values(project) or {}

    marketing = _strip_empty_values(
        _coerce_dict(payload.get("marketing")) or _coerce_dict(raw_marketing_json)
    ) or {}
    tracks = _strip_empty_values(
        _coerce_list(payload.get("tracks")) or _coerce_list(raw_tracks_json)
    ) or []

    debug = {
        "has_payload": bool(payload),
        "has_marketing_json": bool(raw_marketing_json),
        "has_tracks_json": bool(raw_tracks_json),
        "payload_is_string": isinstance(raw_payload, str),
        "marketing_json_is_string": isinstance(raw_marketing_json, str),
        "tracks_json_is_string": isinstance(raw_tracks_json, str),
        "payload_type": type(raw_payload).__name__,
        "marketing_json_type": type(raw_marketing_json).__name__,
        "tracks_json_type": type(raw_tracks_json).__name__,
        "has_identification_data": _has_meaningful_values(identification),
        "has_project_data": _has_meaningful_values(project),
        "has_marketing_data": _has_meaningful_values(marketing),
        "normalized_tracks_count": len(tracks),
        "normalized_release_date": project.get("release_date") or "",
        "normalized_video_release_date": project.get("video_release_date") or "",
        "hydration_ready": bool(
            _has_meaningful_values(identification)
            or _has_meaningful_values(project)
            or _has_meaningful_values(marketing)
            or len(tracks) > 0
        ),
        "shape_source": (
            "payload"
            if payload
            else "marketing_json_or_tracks_json"
            if marketing or tracks
            else "row_fallback"
        ),
    }

    logger.info(
        "Edit submission normalized: submission_id=%s has_payload=%s payload_is_string=%s has_marketing_json=%s marketing_json_is_string=%s has_tracks_json=%s tracks_json_is_string=%s has_identification_data=%s has_project_data=%s has_marketing_data=%s normalized_tracks_count=%s hydration_ready=%s shape_source=%s",
        row.get("id"),
        debug["has_payload"],
        debug["payload_is_string"],
        debug["has_marketing_json"],
        debug["marketing_json_is_string"],
        debug["has_tracks_json"],
        debug["tracks_json_is_string"],
        debug["has_identification_data"],
        debug["has_project_data"],
        debug["has_marketing_data"],
        debug["normalized_tracks_count"],
        debug["hydration_ready"],
        debug["shape_source"],
    )

    return {
        "submission_id": row.get("id"),
        "draft_token": row.get("draft_token"),
        "edit_token": row.get("edit_token"),
        "workspace_slug": workflow_identity["workspace_slug"],
        "workflow_type": workflow_identity["workflow_type"],
        "identification": identification,
        "project": project,
        "marketing": marketing,
        "tracks": tracks,
        "meta": {
            "form_version": workflow_identity["form_version"],
            "source": meta.get("source") or workflow_identity["source"],
            "submitted_at": meta.get("submitted_at") or row.get("submitted_at"),
        },
        "debug": debug,
    }


def _normalize_notification_emails(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []

    unique: list[str] = []
    seen: set[str] = set()

    for item in value:
        normalized = str(item).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)

    return unique[:5]


def _default_notification_emails(workspace_slug: str) -> List[str]:
    if workspace_slug == "atabaque":
        return ["labels@atabaque.biz"]

    return []


def _load_workspace_email_settings(workspace_slug: str) -> Dict[str, Any]:
    workspace_name = workspace_slug
    submission_email_enabled = True
    notification_emails = _default_notification_emails(workspace_slug)

    try:
        branding_result = (
            supabase
            .table("workspace_branding")
            .select("workspace_name, submission_email_enabled")
            .eq("workspace_slug", workspace_slug)
            .limit(1)
            .execute()
        )

        branding_row = (getattr(branding_result, "data", None) or [None])[0]
        if branding_row:
            workspace_name = branding_row.get("workspace_name") or workspace_name
            if isinstance(branding_row.get("submission_email_enabled"), bool):
                submission_email_enabled = branding_row["submission_email_enabled"]

        settings_result = (
            supabase
            .table("workspace_field_overrides")
            .select("helper_text_override")
            .eq("workspace_slug", workspace_slug)
            .eq("step_key", EMAIL_SETTINGS_STEP_KEY)
            .eq("field_key", EMAIL_SETTINGS_FIELD_KEY)
            .limit(1)
            .execute()
        )

        settings_row = (getattr(settings_result, "data", None) or [None])[0]
        if settings_row and settings_row.get("helper_text_override"):
            parsed_emails = _normalize_notification_emails(
                json.loads(settings_row["helper_text_override"])
            )
            if parsed_emails:
                notification_emails = parsed_emails
    except Exception:
        logger.exception("Could not load workspace notification settings")

    return {
        "workspace_name": workspace_name,
        "submission_email_enabled": submission_email_enabled,
        "notification_emails": notification_emails,
    }


def _maybe_send_submission_summary_email(
    submission_id: str,
    validated_payload: WorkflowSubmissionPayload,
) -> Dict[str, Any]:
    row = _load_submission_row(submission_id)
    if not row:
        return {
            "status": "failed",
            "error": f"Submission {submission_id} not found before summary email dispatch.",
            "recipients_count": 0,
        }

    if row.get("summary_email_sent"):
        return {
            "status": "already_sent",
            "message_id": row.get("summary_email_message_id"),
            "recipients_count": 0,
        }

    workspace_email_settings = _load_workspace_email_settings(
        validated_payload.workspace_slug
    )
    _summary_wf = _submission_workflow_type(validated_payload)
    _summary_ev = get_email_event_config(
        validated_payload.workspace_slug, _summary_wf, "on_summary"
    )
    # v2: per-event recipients; fallback v1: notification_emails legacy
    notification_emails = (
        _summary_ev.get("recipients") or workspace_email_settings["notification_emails"]
    )
    recipients_count = len(notification_emails)

    if not _is_release_intake_payload(validated_payload):
        return {
            "status": "skipped",
            "reason": "not_release_intake",
            "recipients_count": recipients_count,
        }

    if not workspace_email_settings["submission_email_enabled"]:
        return {
            "status": "disabled",
            "recipients_count": recipients_count,
        }

    if not notification_emails:
        return {
            "status": "skipped",
            "reason": "no_recipients",
            "recipients_count": 0,
        }

    try:
        identification = validated_payload.identification
        project = validated_payload.project
        workflow_type = _submission_workflow_type(validated_payload)
        release_date = _get_submission_release_date(validated_payload)
        edit_url = _build_edit_url(
            str(row.get("edit_token") or ""),
            validated_payload.workspace_slug,
            workflow_type,
        )
        email_result = send_submission_summary_email(
            to_emails=notification_emails,
            workspace_name=workspace_email_settings["workspace_name"],
            submitter_name=identification.submitter_name,
            submitter_email=identification.submitter_email,
            project_title=identification.project_title,
            release_type=identification.release_type,
            release_date=release_date,
            genre=project.genre,
            focus_track_name=_get_focus_track_name(validated_payload),
            track_titles=[track.title for track in validated_payload.tracks],
            edit_url=edit_url,
            idempotency_key=f"{submission_id}:summary",
        )
        provider_message_id = email_result.get("provider_message_id")
        if not provider_message_id:
            logger.warning(
                "Submission summary email accepted without provider_message_id submission_id=%s provider_response=%s",
                submission_id,
                email_result.get("provider_response"),
            )
    except Exception as exc:
        logger.exception(
            "Submission summary email provider call failed submission_id=%s",
            submission_id,
        )
        return {
            "status": "failed",
            "error": str(exc),
            "recipients_count": recipients_count,
        }

    latest_row = _load_submission_row(submission_id)
    if latest_row and latest_row.get("summary_email_sent"):
        return {
            "status": "already_sent_by_other",
            "message_id": latest_row.get("summary_email_message_id"),
            "recipients_count": recipients_count,
        }

    sent_at = _utc_now_iso()
    try:
        supabase.table("submissions").update(
            {
                "summary_email_sent": True,
                "summary_email_sent_at": sent_at,
                "summary_email_message_id": provider_message_id,
                "updated_at": sent_at,
            }
        ).eq("id", submission_id).execute()
    except Exception:
        logger.exception(
            "Submission summary email sent but flag update failed submission_id=%s",
            submission_id,
        )
        return {
            "status": "sent_but_flag_failed",
            "message_id": provider_message_id,
            "recipients_count": recipients_count,
        }

    return {
        "status": "sent",
        "message_id": provider_message_id,
        "recipients_count": recipients_count,
    }


def _build_track_rows(
    *,
    payload: WorkflowSubmissionPayload,
    submission_id: str,
    now_iso: str,
    prepared_track_payloads: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if _is_company_registry_payload(payload):
        return []

    if _is_rights_clearance_payload(payload):
        if getattr(payload.request_type, "clearance_format", "") != "music_release_clearance_intake":
            return []

        rows: List[Dict[str, Any]] = []

        for index, track in enumerate(payload.tracks or [], start=1):
            rows.append(
                {
                    "submission_id": submission_id,
                    "draft_token": _as_uuid(payload.draft_token),
                    "order_number": getattr(track, "order_number", None) or index,
                    "title": track.title,
                    "artists": track.primary_artists,
                    "authors": track.authors,
                    "lyrics": getattr(track, "notes_for_clearance", None),
                    "explicit": False,
                    "created_at": now_iso,
                }
            )

        return rows

    rows: List[Dict[str, Any]] = []

    for track_payload in prepared_track_payloads or _build_release_track_payloads(payload):
        rows.append(
            _build_release_track_row(
                submission_id=submission_id,
                draft_token=_as_uuid(payload.draft_token),
                now_iso=now_iso,
                track_payload=track_payload,
            )
        )

    return rows


def _build_airtable_track_rows(
    payload: WorkflowSubmissionPayload,
) -> List[Dict[str, Any]]:
    if _is_rights_clearance_payload(payload):
        return []

    rows: List[Dict[str, Any]] = []

    for track in payload.tracks:
        audio_public_url = None
        audio_path = None

        if getattr(track, "audio_file", None):
            audio_file = track.audio_file
            audio_public_url = getattr(audio_file, "public_url", None)
            audio_path = getattr(audio_file, "storage_path", None)

        rows.append(
            {
                "client_track_id": getattr(track, "client_track_id", None),
                "order_number": track.order_number,
                "title": track.title,
                "artists": track.primary_artists,
                "feats": track.featured_artists,
                "interpreters": getattr(track, "interpreters", None),
                "authors": track.authors,
                "publishers": getattr(track, "publishers", None),
                "producers_musicians": getattr(track, "producers_musicians", None),
                "phonographic_producer": getattr(track, "phonographic_producer", None),
                "artist_profiles_status": getattr(track, "artist_profiles_status", None),
                "artist_profile_names_to_create": getattr(
                    track, "artist_profile_names_to_create", None
                ),
                "existing_profile_links": getattr(track, "existing_profile_links", None),
                "explicit_content": _yes_no_or_none(track.explicit_content),
                "has_isrc": _yes_no_or_none(getattr(track, "has_isrc", None)),
                "isrc": track.isrc_code if getattr(track, "has_isrc", None) == "yes" else None,
                "tiktok_snippet": getattr(track, "tiktok_snippet", None),
                "audio_public_url": audio_public_url,
                "audio_path": audio_path,
                "lyrics": track.lyrics,
                "track_status": getattr(track, "track_status", None),
                "is_focus_track": getattr(track, "is_focus_track", False),
            }
        )

    return rows


def _build_airtable_sync_track_rows(
    payload: ReleaseIntakeSubmissionPayload,
    track_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    active_airtable_rows = _build_airtable_track_rows(payload)
    active_by_client_track_id = {
        str(track.get("client_track_id") or "").strip(): track
        for track in active_airtable_rows
        if str(track.get("client_track_id") or "").strip()
    }
    sync_rows: List[Dict[str, Any]] = []

    for track_row in track_rows:
        client_track_id = str(track_row.get("client_track_id") or "").strip()
        active_track = active_by_client_track_id.get(client_track_id, {})
        sync_rows.append(
            {
                "id": track_row.get("id"),
                "client_track_id": client_track_id,
                "airtable_track_id": track_row.get("airtable_track_id"),
                "deleted_at": track_row.get("deleted_at"),
                "order_number": active_track.get("order_number") or track_row.get("order_number"),
                "title": active_track.get("title") or track_row.get("title"),
                "artists": active_track.get("artists") or track_row.get("artists"),
                "feats": active_track.get("feats"),
                "interpreters": active_track.get("interpreters"),
                "authors": active_track.get("authors") or track_row.get("authors"),
                "publishers": active_track.get("publishers"),
                "producers_musicians": active_track.get("producers_musicians"),
                "phonographic_producer": active_track.get("phonographic_producer"),
                "artist_profiles_status": active_track.get("artist_profiles_status"),
                "artist_profile_names_to_create": active_track.get(
                    "artist_profile_names_to_create"
                ),
                "existing_profile_links": active_track.get("existing_profile_links"),
                "explicit_content": active_track.get("explicit_content"),
                "has_isrc": active_track.get("has_isrc"),
                "isrc": active_track.get("isrc"),
                "tiktok_snippet": active_track.get("tiktok_snippet"),
                "audio_public_url": active_track.get("audio_public_url"),
                "audio_path": active_track.get("audio_path"),
                "lyrics": active_track.get("lyrics") or track_row.get("lyrics"),
                "track_status": (
                    "Removida"
                    if track_row.get("deleted_at")
                    else active_track.get("track_status")
                ),
                "is_focus_track": bool(active_track.get("is_focus_track")),
            }
        )

    return sync_rows


def _sync_airtable(
    *,
    payload: WorkflowSubmissionPayload,
    submission_id: str,
    edit_token: str,
) -> Dict[str, Any]:
    if _is_rights_clearance_payload(payload):
        # Rights clearance sync is handled separately via sync_rights_clearance_to_airtable.
        # This path should not be reached for clearance payloads.
        raise RuntimeError(
            "Rights clearance submissions must use sync_rights_clearance_to_airtable, not _sync_airtable."
        )

    submission_row = _load_submission_row(submission_id)
    if not submission_row:
        raise RuntimeError(f"Submission {submission_id} not found before Airtable sync")

    submission_payload_data = _coerce_dict(submission_row.get("payload")) or _build_payload_dump(
        payload
    )
    submission_payload = validate_submission_payload(submission_payload_data)
    if _is_rights_clearance_payload(submission_payload):
        raise RuntimeError(
            "Airtable sync for rights_clearance is not connected yet."
        )

    track_rows = _load_submission_tracks(submission_id)
    airtable_tracks_input = _build_airtable_sync_track_rows(
        submission_payload,
        track_rows,
    )
    submission_for_airtable = dict(submission_row)
    submission_for_airtable["payload"] = _build_payload_dump(submission_payload)
    submission_for_airtable["edit_url"] = _build_edit_url(
        edit_token,
        submission_payload.workspace_slug,
    )

    airtable_project = upsert_airtable_project(submission_for_airtable)
    submission_for_airtable["airtable_project_id"] = airtable_project["id"]
    airtable_tracks = upsert_airtable_tracks(
        submission_for_airtable,
        airtable_tracks_input,
    )

    focus_track_record_id: Optional[str] = None
    active_track_inputs = {
        str(track.get("client_track_id") or "").strip(): track
        for track in airtable_tracks_input
        if not track.get("deleted_at")
    }
    active_airtable_tracks = [
        track for track in airtable_tracks if not track.get("deleted_at")
    ]
    for airtable_track in active_airtable_tracks:
        client_track_id = str(airtable_track.get("client_track_id") or "").strip()
        if active_track_inputs.get(client_track_id, {}).get("is_focus_track"):
            focus_track_record_id = airtable_track["id"]
            break

    if not focus_track_record_id and active_airtable_tracks:
        focus_track_record_id = active_airtable_tracks[0]["id"]

    if focus_track_record_id:
        try:
            update_airtable_project_focus_track(
                airtable_project_id=airtable_project["id"],
                airtable_focus_track_id=focus_track_record_id,
            )
        except Exception:
            logger.exception("Focus track sync failed")

    return {
        "airtable_project": airtable_project,
        "airtable_tracks": airtable_tracks,
        "focus_track_record_id": focus_track_record_id,
    }


def _update_release_submission(
    *,
    existing_row: Dict[str, Any],
    payload: ReleaseIntakeSubmissionPayload,
    now_iso: str,
    idempotency_key: str | None,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    submission_id = str(existing_row["id"])
    existing_version = int(existing_row.get("version") or 1)
    existing_track_rows = _load_submission_tracks(submission_id)
    prepared_track_payloads, hydrated_track_rows = _prepare_release_track_payloads_for_update(
        payload,
        existing_track_rows,
    )
    payload_dump = _build_release_payload_dump(payload, prepared_track_payloads)
    update_row = _build_submission_update_row(
        existing_row=existing_row,
        payload=payload,
        payload_dump=payload_dump,
        now_iso=now_iso,
        idempotency_key=idempotency_key,
    )

    update_result = (
        supabase.table("submissions")
        .update(update_row)
        .eq("id", submission_id)
        .eq("version", existing_version)
        .execute()
    )
    updated_rows = getattr(update_result, "data", None) or []
    if not updated_rows:
        raise HTTPException(
            status_code=409,
            detail="Submission was updated by another request. Please reload and try again.",
        )

    reconcile_result = _reconcile_release_tracks(
        submission_id=submission_id,
        draft_token=_as_uuid(existing_row.get("draft_token") or payload.draft_token),
        track_payloads=prepared_track_payloads,
        existing_track_rows=hydrated_track_rows,
        now_iso=now_iso,
    )
    _insert_submission_revision(
        submission_id=submission_id,
        version=int(update_row["version"]),
        payload=payload_dump,
    )

    _ri_cfg = get_workflow_settings(payload.workspace_slug, "release_intake")
    airtable_result: Optional[Dict[str, Any]] = None
    airtable_error: Optional[str] = None
    if _ri_cfg.get("airtable_sync_enabled", True):
        try:
            airtable_result = _sync_airtable(
                payload=payload,
                submission_id=submission_id,
                edit_token=str(existing_row.get("edit_token") or payload.edit_token or ""),
            )
            _update_submission_airtable_success(
                submission_id,
                airtable_result["airtable_project"]["id"],
            )
        except Exception as exc:
            airtable_error = str(exc)
            _update_submission_airtable_failed(submission_id, airtable_error)
            logger.exception("Airtable sync failed during submission update")
    else:
        logger.info(
            "Airtable sync skipped by workspace config submission_id=%s workspace=%s workflow=release_intake",
            submission_id,
            payload.workspace_slug,
        )

    updated_row = dict(existing_row)
    updated_row.update(updated_rows[0])
    if airtable_result:
        updated_row["airtable_project_id"] = airtable_result["airtable_project"]["id"]
        updated_row["airtable_sync_status"] = "synced"
    elif airtable_error:
        updated_row["airtable_sync_status"] = "failed"

    response = _build_updated_submission_response(
        submission_row=updated_row,
        payload=payload,
        tracks_created=reconcile_result["active_tracks_count"],
    )
    if airtable_result:
        response["airtable_project_id"] = airtable_result["airtable_project"]["id"]
        response["airtable_tracks_created"] = len(
            [track for track in airtable_result["airtable_tracks"] if not track.get("deleted_at")]
        )
        response["airtable_focus_track_id"] = airtable_result.get("focus_track_record_id")
        response["sync"]["airtable"] = "ok"
    if airtable_error:
        response["airtable_error"] = airtable_error
        response["sync"]["airtable"] = "failed"

    # Google Drive sync must run on edit pós-submit just like it does on the
    # initial submit, so that folder reuse / rename logic (PR #10) is
    # exercised when the submission is mutated. Folder reuse is keyed by
    # submissions.google_drive_folder_id, so queuing this here will land on
    # the same project folder rather than creating a duplicate.
    drive_sync = _queue_google_drive_sync(
        background_tasks=background_tasks,
        payload=payload,
        submission_id=submission_id,
    )
    response["drive_sync"] = drive_sync

    # Email on_edit para release_intake (era ausente — fechando gap)
    if _ri_cfg.get("edit_email_enabled", True):
        try:
            send_edit_link_email(
                to_email=payload.identification.submitter_email,
                recipient_name=payload.identification.submitter_name,
                edit_token=str(existing_row.get("edit_token") or payload.edit_token or ""),
                project_title=payload.identification.project_title,
                workspace_slug=payload.workspace_slug,
                workflow_type="release_intake",
                event="on_edit",
            )
        except Exception:
            logger.warning(
                "Release intake edit email failed submission_id=%s",
                submission_id,
            )

    return response


@router.get("/edit/{edit_token}")
async def load_edit_submission(edit_token: str):
    row = _load_submission_by_edit_token(edit_token)
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")

    logger.info(
        "Loading edit submission: edit_token=%s submission_id=%s payload_type=%s marketing_json_type=%s tracks_json_type=%s",
        edit_token,
        row.get("id"),
        type(row.get("payload")).__name__,
        type(row.get("marketing_json")).__name__,
        type(row.get("tracks_json")).__name__,
    )
    workflow_identity = _workflow_identity_from_row(row)
    track_rows = _load_submission_tracks(str(row.get("id") or ""))
    if track_rows and workflow_identity["workflow_type"] != RIGHTS_CLEARANCE_WORKFLOW_TYPE:
        track_rows = _ensure_track_rows_have_client_track_ids(track_rows)
        row = _persist_release_track_ids_on_submission(row, track_rows)

    normalized_data = _normalize_edit_submission_data(row)
    if track_rows:
        normalized_data = _with_release_track_client_ids(normalized_data, track_rows)

    return {
        "ok": True,
        "data": normalized_data,
    }


def _handle_clearance_edit_resubmit(
    edit_token: str,
    payload: "RightsClearanceSubmissionPayload",
    idempotency_key: str | None,
) -> Dict[str, Any] | None:
    existing_row = _load_submission_by_edit_token(edit_token)
    if existing_row is None:
        return None  # fall through to create new submission

    submission_id = str(existing_row["id"])
    now_iso = _utc_now_iso()

    update_data: Dict[str, Any] = {
        "updated_at": now_iso,
        "payload": _safe_model_dump(payload),
    }

    try:
        supabase.table("submissions").update(update_data).eq("id", submission_id).execute()
        logger.info(
            "Clearance edit resubmit updated submission_id=%s edit_token=%s",
            submission_id,
            edit_token,
        )
    except Exception:
        logger.exception(
            "Clearance edit resubmit DB update failed submission_id=%s",
            submission_id,
        )
        raise HTTPException(status_code=500, detail="Failed to update submission")

    _clearance_cfg = get_workflow_settings(payload.workspace_slug, "rights_clearance")
    if _clearance_cfg.get("edit_email_enabled", True):
        try:
            send_edit_link_email(
                to_email=payload.requester_identification.requester_email,
                recipient_name=payload.requester_identification.requester_name,
                edit_token=edit_token,
                project_title=payload.project_context.project_title,
                workspace_slug=payload.workspace_slug,
                workflow_type="rights_clearance",
                event="on_edit",
                variant=str(getattr(getattr(payload, "request_type", None), "clearance_format", "") or ""),
            )
        except Exception:
            logger.warning(
                "Clearance edit resubmit email failed submission_id=%s",
                submission_id,
            )
    else:
        logger.info(
            "Clearance edit resubmit email skipped by config submission_id=%s",
            submission_id,
        )

    airtable_record_id = str(existing_row.get("airtable_project_id") or "").strip()
    airtable_result: str = "skipped_no_record_id"
    if not _clearance_cfg.get("airtable_sync_enabled", True):
        airtable_result = "skipped_config"
        logger.info(
            "Airtable sync skipped by workspace config submission_id=%s workspace=%s workflow=rights_clearance",
            submission_id,
            payload.workspace_slug,
        )
    elif airtable_record_id:
        try:
            update_rights_clearance_case_in_airtable(
                payload=payload,
                airtable_case_id=airtable_record_id,
                submission_id=submission_id,
            )
            airtable_result = "updated"
        except Exception:
            logger.warning(
                "Clearance edit resubmit Airtable update failed submission_id=%s",
                submission_id,
            )
            airtable_result = "error"

    return {
        "ok": True,
        "submission_id": submission_id,
        "edit_token": edit_token,
        "updated": True,
        "airtable": airtable_result,
        "drive_sync": {"status": "skipped", "reason": "edit_resubmit"},
    }


def _handle_company_registry_edit_resubmit(
    edit_token: str,
    payload: "CompanyRegistrySubmissionPayload",
    idempotency_key: str | None,
) -> Dict[str, Any] | None:
    existing_row = _load_submission_by_edit_token(edit_token)
    if existing_row is None:
        return None  # fall through to create new submission

    submission_id = str(existing_row["id"])
    now_iso = _utc_now_iso()

    update_data: Dict[str, Any] = {
        "updated_at": now_iso,
        "payload": _safe_model_dump(payload),
    }

    try:
        supabase.table("submissions").update(update_data).eq("id", submission_id).execute()
        logger.info(
            "Company registry edit resubmit updated submission_id=%s edit_token=%s",
            submission_id,
            edit_token,
        )
    except Exception:
        logger.exception(
            "Company registry edit resubmit DB update failed submission_id=%s",
            submission_id,
        )
        raise HTTPException(status_code=500, detail="Failed to update submission")

    _company_cfg = get_workflow_settings(payload.workspace_slug, "company_registry")
    if _company_cfg.get("edit_email_enabled", True):
        try:
            send_edit_link_email(
                to_email=payload.legal_representative.email,
                recipient_name=payload.legal_representative.name,
                edit_token=edit_token,
                project_title=payload.company_data.fantasy_name,
                workspace_slug=payload.workspace_slug,
                workflow_type="company_registry",
                event="on_edit",
            )
        except Exception:
            logger.warning(
                "Company registry edit resubmit email failed submission_id=%s",
                submission_id,
            )
    else:
        logger.info(
            "Company registry edit resubmit email skipped by config submission_id=%s",
            submission_id,
        )

    airtable_record_id = str(existing_row.get("airtable_project_id") or "").strip()
    airtable_result: str = "skipped_no_record_id"
    if not _company_cfg.get("airtable_sync_enabled", True):
        airtable_result = "skipped_config"
        logger.info(
            "Airtable sync skipped by workspace config submission_id=%s workspace=%s workflow=company_registry",
            submission_id,
            payload.workspace_slug,
        )
    elif airtable_record_id:
        try:
            update_company_registry_in_airtable(
                payload=payload,
                airtable_record_id=airtable_record_id,
            )
            airtable_result = "updated"
        except Exception:
            logger.warning(
                "Company registry edit resubmit Airtable update failed submission_id=%s",
                submission_id,
            )
            airtable_result = "error"

    return {
        "ok": True,
        "submission_id": submission_id,
        "edit_token": edit_token,
        "updated": True,
        "airtable": airtable_result,
        "drive_sync": {"status": "skipped", "reason": "edit_resubmit"},
    }


@router.post("")
def create_submission(
    payload: Dict[str, Any],
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Dict[str, Any]:
    try:
        validated_payload = validate_submission_payload(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    # -- clearance edit-resubmit early return --
    if isinstance(validated_payload, RightsClearanceSubmissionPayload):
        _edit_token = str(getattr(validated_payload, "edit_token", None) or "").strip() or None
        if _edit_token:
            result = _handle_clearance_edit_resubmit(
                edit_token=_edit_token,
                payload=validated_payload,
                idempotency_key=_clean_idempotency_key(idempotency_key),
            )
            if result is not None:
                return result

    # -- company registry edit-resubmit early return --
    if isinstance(validated_payload, CompanyRegistrySubmissionPayload):
        _edit_token = str(getattr(validated_payload, "edit_token", None) or "").strip() or None
        if _edit_token:
            result = _handle_company_registry_edit_resubmit(
                edit_token=_edit_token,
                payload=validated_payload,
                idempotency_key=_clean_idempotency_key(idempotency_key),
            )
            if result is not None:
                return result

    clean_idempotency_key = _clean_idempotency_key(idempotency_key)

    logger.info(
        "Creating submission workspace_slug=%s workflow_type=%s form_version=%s",
        validated_payload.workspace_slug,
        _submission_workflow_type(validated_payload),
        _submission_form_version(validated_payload),
    )

    reference_time = datetime.now(timezone.utc)

    if _is_release_intake_payload(validated_payload):
        edit_token = str(validated_payload.edit_token or "").strip() or None
        if edit_token:
            existing_row = _load_submission_by_edit_token(edit_token)
            if not existing_row:
                raise HTTPException(status_code=404, detail="Submission not found")

            replay_row = _load_recent_idempotent_submission(
                idempotency_key=clean_idempotency_key,
                reference=reference_time,
                submission_id=str(existing_row["id"]),
                edit_token=edit_token,
            )
            if replay_row:
                return _build_submission_replay_response(
                    replay_row,
                    message="Submission already processed recently.",
                )

            return _update_release_submission(
                existing_row=existing_row,
                payload=validated_payload,
                now_iso=_utc_now_iso(),
                idempotency_key=clean_idempotency_key,
                background_tasks=background_tasks,
            )

    replay_row = _load_recent_idempotent_submission(
        idempotency_key=clean_idempotency_key,
        reference=reference_time,
        draft_token=validated_payload.draft_token,
    )
    if replay_row:
        return _build_submission_replay_response(
            replay_row,
            message="Submission already processed recently.",
        )

    now_iso = _utc_now_iso()
    submission_id = str(uuid4())
    edit_token = _generate_edit_token()
    prepared_track_payloads = (
        _build_release_track_payloads(validated_payload)
        if _is_release_intake_payload(validated_payload)
        else None
    )

    submission_row = _build_submission_row(
        payload=validated_payload,
        submission_id=submission_id,
        edit_token=edit_token,
        now_iso=now_iso,
        prepared_track_payloads=prepared_track_payloads,
        idempotency_key=clean_idempotency_key,
    )

    try:
        submission_res = supabase.table("submissions").insert(submission_row).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create submission: {exc}")

    if not getattr(submission_res, "data", None):
        raise HTTPException(status_code=500, detail="Failed to create submission")

    track_rows = _build_track_rows(
        payload=validated_payload,
        submission_id=submission_id,
        now_iso=now_iso,
        prepared_track_payloads=prepared_track_payloads,
    )

    created_tracks: List[Dict[str, Any]] = []

    if track_rows:
        try:
            tracks_res = supabase.table("tracks").insert(track_rows).execute()
            created_tracks = getattr(tracks_res, "data", None) or []
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Submission created but failed to create tracks: {exc}",
            )

    _insert_submission_revision(
        submission_id=submission_id,
        version=1,
        payload=submission_row["payload"],
    )
    _mark_draft_as_submitted(_as_uuid(validated_payload.draft_token))

    airtable_result: Optional[Dict[str, Any]] = None
    airtable_error: Optional[str] = None

    _wf_type_for_sync = _submission_workflow_type(validated_payload)
    _wf_cfg_for_sync = get_workflow_settings(validated_payload.workspace_slug, _wf_type_for_sync)

    if not _wf_cfg_for_sync.get("airtable_sync_enabled", True):
        logger.info(
            "Airtable sync skipped by workspace config submission_id=%s workspace=%s workflow=%s",
            submission_id,
            validated_payload.workspace_slug,
            _wf_type_for_sync,
        )
    elif _is_release_intake_payload(validated_payload):
        try:
            airtable_result = _sync_airtable(
                payload=validated_payload,
                submission_id=submission_id,
                edit_token=edit_token,
            )

            airtable_project_id = airtable_result["airtable_project"]["id"]
            _update_submission_airtable_success(submission_id, airtable_project_id)

        except Exception as exc:
            airtable_error = str(exc)
            _update_submission_airtable_failed(submission_id, airtable_error)
            logger.exception("Airtable sync failed")

    elif _is_rights_clearance_payload(validated_payload):
        try:
            clearance_sync_result = sync_rights_clearance_to_airtable(
                payload=validated_payload,
                submission_id=submission_id,
                edit_token=edit_token,
            )

            if clearance_sync_result.get("skipped"):
                logger.info(
                    "Rights clearance Airtable sync skipped: reason=%s submission_id=%s",
                    clearance_sync_result.get("skip_reason"),
                    submission_id,
                )
            else:
                airtable_project = clearance_sync_result.get("airtable_project") or {}
                airtable_project_id = airtable_project.get("id")
                if airtable_project_id:
                    _update_submission_airtable_success(submission_id, airtable_project_id)

                clearance_tracks = clearance_sync_result.get("airtable_tracks") or []
                if created_tracks and clearance_tracks:
                    _persist_airtable_track_ids(
                        created_tracks=created_tracks,
                        airtable_tracks=clearance_tracks,
                    )

        except Exception as exc:
            airtable_error = str(exc)
            _update_submission_airtable_failed(submission_id, airtable_error)
            logger.exception("Rights clearance Airtable sync failed")

    elif _is_company_registry_payload(validated_payload):
        try:
            company_sync_result = sync_company_registry_to_airtable(
                payload=validated_payload,
            )
            if company_sync_result.get("status") == "created":
                record_id = company_sync_result.get("record_id", "")
                if record_id:
                    _update_submission_airtable_success(submission_id, record_id)
        except Exception as exc:
            airtable_error = str(exc)
            _update_submission_airtable_failed(submission_id, airtable_error)
            logger.exception("Company registry Airtable sync failed")

    drive_sync = _queue_google_drive_sync(
        background_tasks=background_tasks,
        payload=validated_payload,
        submission_id=submission_id,
    )

    release_date = _get_submission_release_date(validated_payload)
    primary_artist = _get_primary_artist(validated_payload)
    days_until_release = _calculate_days_until_release(release_date)
    edit_url = _build_edit_url(
        edit_token,
        validated_payload.workspace_slug,
        _submission_workflow_type(validated_payload),
    )
    email_subject = _build_post_submit_email_subject(
        project_title=_get_submission_project_title(validated_payload),
        release_date=release_date,
        primary_artist=primary_artist,
    )

    email_error: Optional[str] = None
    email_result: Optional[Dict[str, Any]] = None
    email_sent = False
    email_status = "pending"
    notification_email_error: Optional[str] = None
    notification_email_status = "skipped"
    notification_email_recipients = 0

    supports_workflow_routed_edit_email = (
        "workflow_type" in inspect.signature(send_edit_link_email).parameters
    )

    _wf_type = _submission_workflow_type(validated_payload)
    _wf_cfg = get_workflow_settings(validated_payload.workspace_slug, _wf_type)

    if not _wf_cfg.get("post_submit_email_enabled", True):
        email_status = "skipped_config"
        _update_submission_email_skipped(submission_id, "post_submit_email_enabled=false")
        logger.info(
            "Post-submit email skipped by workspace config submission_id=%s workspace=%s workflow=%s",
            submission_id,
            validated_payload.workspace_slug,
            _wf_type,
        )
    elif (
        _wf_type in (RIGHTS_CLEARANCE_WORKFLOW_TYPE, "company_registry")
        and not supports_workflow_routed_edit_email
    ):
        email_status = "skipped"
        email_error = (
            "Workflow-specific post-submit email routing is not enabled in this "
            "backend instance."
        )
        _update_submission_email_skipped(submission_id, email_error)
        logger.warning(
            "Skipping post-submit email submission_id=%s workflow_type=%s because email.py does not support workflow routing yet",
            submission_id,
            _wf_type,
        )
    else:
        try:
            email_kwargs = {
                "to_email": _get_submission_contact_email(validated_payload),
                "edit_token": edit_token,
                "project_title": _get_submission_project_title(validated_payload),
                "release_date": release_date,
                "primary_artist": primary_artist,
                "days_until_release": days_until_release,
                "recipient_name": _get_submission_contact_name(validated_payload),
                "workspace_slug": validated_payload.workspace_slug,
            }
            if supports_workflow_routed_edit_email:
                email_kwargs["workflow_type"] = _submission_workflow_type(validated_payload)
            email_kwargs["event"] = "on_submit"
            # Variant para rights_clearance: roteia por clearance_format
            if _wf_type == "rights_clearance":
                email_kwargs["variant"] = str(
                    getattr(
                        getattr(validated_payload, "request_type", None),
                        "clearance_format", "",
                    ) or ""
                )

            email_result = send_edit_link_email(**email_kwargs)

            provider_message_id = email_result.get("provider_message_id")
            if not provider_message_id:
                raise RuntimeError(
                    "Email provider accepted the request but did not return a message id"
                )

            logger.info(
                "Post-submit email accepted submission_id=%s to_email=%s subject=%s edit_url=%s provider_message_id=%s provider_response=%s",
                submission_id,
                email_result.get("to_email"),
                email_result.get("subject"),
                email_result.get("edit_url"),
                provider_message_id,
                email_result.get("provider_response"),
            )

            _update_submission_email_sent(submission_id)
            email_sent = True
            email_status = "ok"
        except Exception as exc:
            email_error = str(exc)
            email_status = "failed"
            logger.error(
                "Post-submit email failed submission_id=%s to_email=%s subject=%s edit_url=%s error=%s",
                submission_id,
                _get_submission_contact_email(validated_payload),
                email_subject,
                edit_url,
                email_error,
            )
            _update_submission_email_failed(submission_id, email_error)
            logger.exception("Edit link email failed")

    notification_email_result = _maybe_send_submission_summary_email(
        submission_id,
        validated_payload,
    )
    notification_email_recipients = notification_email_result.get(
        "recipients_count",
        0,
    )
    notification_email_status = {
        "already_sent": "already_sent",
        "already_sent_by_other": "already_sent",
        "sent": "ok",
        "sent_but_flag_failed": "ok_flag_failed",
        "skipped": "skipped",
        "disabled": "disabled",
        "failed": "failed",
    }.get(notification_email_result["status"], "failed")
    notification_email_error = notification_email_result.get("error")

    response: Dict[str, Any] = {
        "ok": True,
        "submission_id": submission_id,
        "draft_token": _as_uuid(validated_payload.draft_token),
        "edit_token": edit_token,
        "tracks_created": len(created_tracks),
        "message": "Submission created successfully.",
        "drive_sync": drive_sync,
        "workflow": {
            "workspace_slug": validated_payload.workspace_slug,
            "workflow_type": _submission_workflow_type(validated_payload),
            "form_version": _submission_form_version(validated_payload),
        },
        "sync": {
            "supabase": "ok",
            "airtable": (
                "skipped"
                if _is_rights_clearance_payload(validated_payload) and not airtable_error
                else "ok" if not airtable_error else "failed"
            ),
            "email": email_status,
            "notification_email": notification_email_status,
        },
    }

    if airtable_result:
        response["airtable_project_id"] = airtable_result["airtable_project"]["id"]
        response["airtable_tracks_created"] = len(airtable_result["airtable_tracks"])
        response["airtable_focus_track_id"] = airtable_result.get("focus_track_record_id")

    if airtable_error:
        response["airtable_error"] = airtable_error

    if email_error:
        response["email_error"] = email_error

    response["email_debug"] = {
        "to_email": _get_submission_contact_email(validated_payload),
        "subject": (email_result or {}).get("subject") or email_subject,
        "edit_url": (email_result or {}).get("edit_url") or edit_url,
        "provider_response": (email_result or {}).get("provider_response"),
        "provider_message_id": (email_result or {}).get("provider_message_id"),
    }

    response["notification_email_recipients"] = notification_email_recipients
    response["notification_email_already_sent"] = (
        notification_email_result["status"] in {"already_sent"}
    )
    response["notification_email_message_id"] = notification_email_result.get(
        "message_id"
    )

    if notification_email_error:
        response["notification_email_error"] = notification_email_error

    return response
