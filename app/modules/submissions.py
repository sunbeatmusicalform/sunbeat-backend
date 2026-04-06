from __future__ import annotations

import logging
import json
import secrets
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.database import supabase
from app.schemas.submission import SubmissionPayload
from app.services.airtable import (
    create_airtable_project,
    create_airtable_tracks,
    update_airtable_project_focus_track,
)
from app.services.email import send_edit_link_email, send_submission_summary_email

logger = logging.getLogger("sunbeat.submissions")

router = APIRouter(prefix="/submissions", tags=["Submissions"])

EMAIL_SETTINGS_STEP_KEY = "__workspace_settings__"
EMAIL_SETTINGS_FIELD_KEY = "submission_notification_emails"
DEFAULT_WORKFLOW_TYPE = "release_intake"
DEFAULT_FORM_VERSION = "legacy_v1"


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


def _submission_workflow_type(payload: SubmissionPayload) -> str:
    workflow_type = getattr(payload, "workflow_type", None)
    text = str(workflow_type or "").strip()
    return text or DEFAULT_WORKFLOW_TYPE


def _submission_form_version(payload: SubmissionPayload) -> str:
    meta = getattr(payload, "meta", None)
    form_version = getattr(meta, "form_version", None)
    text = str(form_version or "").strip()
    return text or DEFAULT_FORM_VERSION


def _get_focus_track_name(payload: SubmissionPayload) -> Optional[str]:
    focus_track = _get_focus_track(payload)
    if focus_track and getattr(focus_track, "title", None):
        return focus_track.title

    marketing = payload.marketing

    if marketing and getattr(marketing, "focus_track_name", None):
        return marketing.focus_track_name

    return None


def _get_focus_track(payload: SubmissionPayload) -> Any | None:
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


def _get_primary_artist(payload: SubmissionPayload) -> Optional[str]:
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
        f"Resumo do lançamento - {safe_project_title} - "
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
    if text in {"no", "não", "nao", "false", "0"}:
        return "Não"
    return str(value).strip()


def _build_edit_url(edit_token: str, workspace_slug: str) -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/intake/{workspace_slug}?edit_token={edit_token}"


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
    payload: SubmissionPayload,
    submission_id: str,
    edit_token: str,
    now_iso: str,
) -> Dict[str, Any]:
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
        "marketing_json": _safe_model_dump(marketing),
        "tracks_json": [_safe_model_dump(track) for track in payload.tracks],
        "payload": payload.model_dump() if hasattr(payload, "model_dump") else {},
        "airtable_sync_status": "pending",
        "email_status": "pending",
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


def _normalize_edit_submission_data(row: Dict[str, Any]) -> Dict[str, Any]:
    raw_payload = row.get("payload")
    raw_marketing_json = row.get("marketing_json")
    raw_tracks_json = row.get("tracks_json")

    payload = _coerce_dict(raw_payload)
    meta = _coerce_dict(payload.get("meta"))

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
        "workspace_slug": payload.get("workspace_slug") or row.get("client_slug"),
        "workflow_type": payload.get("workflow_type") or DEFAULT_WORKFLOW_TYPE,
        "identification": identification,
        "project": project,
        "marketing": marketing,
        "tracks": tracks,
        "meta": {
            "form_version": meta.get("form_version") or DEFAULT_FORM_VERSION,
            "source": meta.get("source"),
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


def _build_track_rows(
    *,
    payload: SubmissionPayload,
    submission_id: str,
    now_iso: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for track in payload.tracks:
        rows.append(
            {
                "submission_id": submission_id,
                "draft_token": _as_uuid(payload.draft_token),
                "order_number": track.order_number,
                "title": track.title,
                "artists": track.primary_artists,
                "authors": track.authors,
                "lyrics": track.lyrics,
                "explicit": _bool_from_yes_no(track.explicit_content),
                "created_at": now_iso,
            }
        )

    return rows


def _build_airtable_track_rows(payload: SubmissionPayload) -> List[Dict[str, Any]]:
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


def _sync_airtable(
    *,
    payload: SubmissionPayload,
    submission_id: str,
    edit_token: str,
) -> Dict[str, Any]:
    identification = _safe_model_dump(payload.identification)
    project = _safe_model_dump(payload.project)
    marketing = _safe_model_dump(payload.marketing)
    airtable_tracks_input = _build_airtable_track_rows(payload)

    edit_url = _build_edit_url(edit_token, payload.workspace_slug)

    airtable_project = create_airtable_project(
        workspace_slug=payload.workspace_slug,
        identification=identification,
        project=project,
        marketing=marketing,
        submission_id=submission_id,
        draft_token=_as_uuid(payload.draft_token),
        edit_url=edit_url,
    )

    airtable_project_id = airtable_project["id"]

    airtable_tracks = create_airtable_tracks(
        airtable_project_id=airtable_project_id,
        workspace_slug=payload.workspace_slug,
        submission_id=submission_id,
        tracks=airtable_tracks_input,
    )

    focus_track_record_id: Optional[str] = None
    for input_track, airtable_track in zip(airtable_tracks_input, airtable_tracks):
        if input_track.get("is_focus_track"):
            focus_track_record_id = airtable_track["id"]
            break

    if not focus_track_record_id and airtable_tracks:
        focus_track_record_id = airtable_tracks[0]["id"]

    if focus_track_record_id:
        try:
            update_airtable_project_focus_track(
                airtable_project_id=airtable_project_id,
                airtable_focus_track_id=focus_track_record_id,
            )
        except Exception:
            logger.exception("Focus track sync failed")

    return {
        "airtable_project": airtable_project,
        "airtable_tracks": airtable_tracks,
        "focus_track_record_id": focus_track_record_id,
    }


@router.get("/edit/{edit_token}")
async def load_edit_submission(edit_token: str):
    result = (
        supabase
        .table("submissions")
        .select("*")
        .eq("edit_token", edit_token)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Submission not found")

    row = result.data[0]
    logger.info(
        "Loading edit submission: edit_token=%s submission_id=%s payload_type=%s marketing_json_type=%s tracks_json_type=%s",
        edit_token,
        row.get("id"),
        type(row.get("payload")).__name__,
        type(row.get("marketing_json")).__name__,
        type(row.get("tracks_json")).__name__,
    )
    return {
        "ok": True,
        "data": _normalize_edit_submission_data(row),
    }


@router.post("")
def create_submission(payload: SubmissionPayload) -> Dict[str, Any]:
    logger.info(
        "Creating submission workspace_slug=%s workflow_type=%s form_version=%s",
        payload.workspace_slug,
        _submission_workflow_type(payload),
        _submission_form_version(payload),
    )

    now_iso = _utc_now_iso()
    submission_id = str(uuid4())
    edit_token = _generate_edit_token()

    submission_row = _build_submission_row(
        payload=payload,
        submission_id=submission_id,
        edit_token=edit_token,
        now_iso=now_iso,
    )

    try:
        submission_res = supabase.table("submissions").insert(submission_row).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create submission: {exc}")

    if not getattr(submission_res, "data", None):
        raise HTTPException(status_code=500, detail="Failed to create submission")

    track_rows = _build_track_rows(
        payload=payload,
        submission_id=submission_id,
        now_iso=now_iso,
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

    _mark_draft_as_submitted(_as_uuid(payload.draft_token))

    airtable_result: Optional[Dict[str, Any]] = None
    airtable_error: Optional[str] = None

    try:
        airtable_result = _sync_airtable(
            payload=payload,
            submission_id=submission_id,
            edit_token=edit_token,
        )

        airtable_project_id = airtable_result["airtable_project"]["id"]
        _update_submission_airtable_success(submission_id, airtable_project_id)

        _persist_airtable_track_ids(
            created_tracks=created_tracks,
            airtable_tracks=airtable_result["airtable_tracks"],
        )

    except Exception as exc:
        airtable_error = str(exc)
        _update_submission_airtable_failed(submission_id, airtable_error)
        logger.exception("Airtable sync failed")

    identification = payload.identification
    project = payload.project
    release_date = _normalize_release_date(getattr(project, "release_date", None))
    primary_artist = _get_primary_artist(payload)
    days_until_release = _calculate_days_until_release(release_date)
    edit_url = _build_edit_url(edit_token, payload.workspace_slug)
    email_subject = _build_post_submit_email_subject(
        project_title=identification.project_title,
        release_date=release_date,
        primary_artist=primary_artist,
    )

    email_error: Optional[str] = None
    email_result: Optional[Dict[str, Any]] = None
    email_sent = False
    notification_email_error: Optional[str] = None
    notification_email_status = "skipped"
    notification_email_recipients = 0

    try:
        email_result = send_edit_link_email(
            to_email=identification.submitter_email,
            edit_token=edit_token,
            project_title=identification.project_title,
            release_date=release_date,
            primary_artist=primary_artist,
            days_until_release=days_until_release,
            recipient_name=identification.submitter_name,
            workspace_slug=payload.workspace_slug,
        )

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
    except Exception as exc:
        email_error = str(exc)
        logger.error(
            "Post-submit email failed submission_id=%s to_email=%s subject=%s edit_url=%s error=%s",
            submission_id,
            identification.submitter_email,
            email_subject,
            edit_url,
            email_error,
        )
        _update_submission_email_failed(submission_id, email_error)
        logger.exception("Edit link email failed")

    try:
        workspace_email_settings = _load_workspace_email_settings(payload.workspace_slug)
        notification_emails = workspace_email_settings["notification_emails"]
        notification_email_recipients = len(notification_emails)

        if (
            workspace_email_settings["submission_email_enabled"]
            and notification_emails
        ):
            send_submission_summary_email(
                to_emails=notification_emails,
                workspace_name=workspace_email_settings["workspace_name"],
                submitter_name=identification.submitter_name,
                submitter_email=identification.submitter_email,
                project_title=identification.project_title,
                release_type=identification.release_type,
                release_date=release_date,
                genre=project.genre,
                focus_track_name=_get_focus_track_name(payload),
                track_titles=[track.title for track in payload.tracks],
                edit_url=edit_url,
            )
            notification_email_status = "ok"
        elif workspace_email_settings["submission_email_enabled"]:
            notification_email_status = "skipped"
        else:
            notification_email_status = "disabled"
    except Exception as exc:
        notification_email_error = str(exc)
        notification_email_status = "failed"
        logger.exception("Submission summary email failed")

    response: Dict[str, Any] = {
        "ok": True,
        "submission_id": submission_id,
        "draft_token": _as_uuid(payload.draft_token),
        "edit_token": edit_token,
        "tracks_created": len(created_tracks),
        "message": "Submission created successfully.",
        "workflow": {
            "workspace_slug": payload.workspace_slug,
            "workflow_type": _submission_workflow_type(payload),
            "form_version": _submission_form_version(payload),
        },
        "sync": {
            "supabase": "ok",
            "airtable": "ok" if not airtable_error else "failed",
            "email": "ok" if email_sent else "failed",
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
        "to_email": identification.submitter_email,
        "subject": (email_result or {}).get("subject") or email_subject,
        "edit_url": (email_result or {}).get("edit_url") or edit_url,
        "provider_response": (email_result or {}).get("provider_response"),
        "provider_message_id": (email_result or {}).get("provider_message_id"),
    }

    response["notification_email_recipients"] = notification_email_recipients

    if notification_email_error:
        response["notification_email_error"] = notification_email_error

    return response
