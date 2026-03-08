from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, EmailStr

from app.core.supabase import supabase

router = APIRouter()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _bool_from_yes_no(value: Optional[str]) -> Optional[bool]:
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


class UploadedFileRef(BaseModel):
    file_id: str
    file_name: str
    storage_path: str
    public_url: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None


class IdentificationPayload(BaseModel):
    submitter_name: str
    submitter_email: EmailStr
    project_title: str
    release_type: Literal["single", "ep", "album"]


class ProjectPayload(BaseModel):
    release_date: str
    genre: str
    explicit_content: Literal["yes", "no"]
    tiktok_snippet: Optional[str] = ""
    presskit_link: Optional[str] = ""
    has_video_asset: Literal["yes", "no", "unknown"]
    cover_file: Optional[UploadedFileRef] = None


class TrackPayload(BaseModel):
    local_id: str
    order_number: int
    title: str
    is_focus_track: bool = False

    primary_artists: str
    featured_artists: Optional[str] = ""
    interpreters: str

    authors: str
    publishers: Optional[str] = ""
    producers_musicians: Optional[str] = ""

    artist_profiles_status: Literal["already_exists", "needs_creation", "mixed", ""]
    has_isrc: Literal["yes", "no", ""]
    isrc_code: Optional[str] = ""

    explicit_content: Literal["yes", "no", ""]
    audio_file: Optional[UploadedFileRef] = None
    lyrics: Optional[str] = ""

    track_status: Literal["draft", "ready"] = "draft"


class MarketingPayload(BaseModel):
    marketing_numbers: str
    marketing_focus: str
    marketing_objectives: str
    marketing_budget: Optional[str] = ""
    focus_track_name: Optional[str] = ""
    date_flexibility: str
    has_special_guests: Literal["yes", "no"]
    promotion_participants: str
    lyrics: Optional[str] = ""
    general_notes: Optional[str] = ""
    additional_files: List[UploadedFileRef] = Field(default_factory=list)


class SubmitMetaPayload(BaseModel):
    form_version: int
    source: Literal["sunbeat_release_intake"]
    submitted_at: Optional[str] = None


class ReleaseIntakeSubmitPayload(BaseModel):
    draft_token: Optional[str] = None
    workspace_slug: Optional[str] = None

    identification: IdentificationPayload
    project: ProjectPayload
    tracks: List[TrackPayload] = Field(default_factory=list)
    marketing: MarketingPayload
    meta: SubmitMetaPayload


def _validate_business_rules(payload: ReleaseIntakeSubmitPayload) -> None:
    release_type = payload.identification.release_type
    tracks_count = len(payload.tracks)

    if release_type == "single" and tracks_count != 1:
        raise HTTPException(
            status_code=400,
            detail="Single must contain exactly 1 track."
        )

    if release_type in {"ep", "album"} and tracks_count < 2:
        raise HTTPException(
            status_code=400,
            detail="EP/Album must contain at least 2 tracks."
        )

    focus_tracks = [t for t in payload.tracks if t.is_focus_track]
    if tracks_count > 0 and len(focus_tracks) > 1:
        raise HTTPException(
            status_code=400,
            detail="Only one focus track is allowed."
        )

    for idx, track in enumerate(payload.tracks, start=1):
        if not _str(track.title):
            raise HTTPException(
                status_code=400,
                detail=f"Track {idx}: title is required."
            )
        if not _str(track.primary_artists):
            raise HTTPException(
                status_code=400,
                detail=f"Track {idx}: primary_artists is required."
            )
        if not _str(track.interpreters):
            raise HTTPException(
                status_code=400,
                detail=f"Track {idx}: interpreters is required."
            )
        if not _str(track.authors):
            raise HTTPException(
                status_code=400,
                detail=f"Track {idx}: authors is required."
            )
        if track.has_isrc == "yes" and not _str(track.isrc_code):
            raise HTTPException(
                status_code=400,
                detail=f"Track {idx}: isrc_code is required when has_isrc=yes."
            )


@router.post("")
async def create_submission(
    payload: ReleaseIntakeSubmitPayload,
    x_tenant_value: Optional[str] = Header(None),
    x_tenant_type: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
    x_user_email: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    # tenant obrigatório
    client_slug = _str(x_tenant_value) or _str(payload.workspace_slug)
    if not client_slug:
        raise HTTPException(status_code=400, detail="Missing client_slug (tenant)")

    _validate_business_rules(payload)

    now_iso = _utc_iso()
    submission_id = str(uuid4())
    edit_token = str(uuid4())

    identification = payload.identification
    project = payload.project
    marketing = payload.marketing

    # faixa foco
    focus_track = next((t for t in payload.tracks if t.is_focus_track), None)
    focus_track_title = (
        _str(marketing.focus_track_name)
        or (_str(focus_track.title) if focus_track else "")
    )

    submission_row: Dict[str, Any] = {
        # ids / controle
        "id": submission_id,
        "draft_token": payload.draft_token,
        "status": "submitted",
        "created_at": now_iso,
        "updated_at": now_iso,
        "submitted_at": payload.meta.submitted_at or now_iso,
        "version": 1,
        "is_update": False,
        "edit_token": edit_token,

        # tenant / user
        "client_slug": client_slug,
        "user_id": _str(x_user_id) or None,
        "user_email": _str(x_user_email) or identification.submitter_email,

        # identificação / projeto
        "artist_name": identification.project_title,
        "email": identification.submitter_email,
        "release_type": identification.release_type,
        "release_title": identification.project_title,
        "main_title": identification.project_title,
        "genre": project.genre,

        # storage / capa
        "cover_url": project.cover_file.public_url if project.cover_file else None,
        "cover_path": project.cover_file.storage_path if project.cover_file else None,

        # marketing / observações
        "track_json": {
            "count": len(payload.tracks),
            "focus_track": focus_track_title,
            "tracks": [t.model_dump(mode="python") for t in payload.tracks],
        },
        "marketing_json": {
            "marketing_numbers": marketing.marketing_numbers,
            "marketing_focus": marketing.marketing_focus,
            "marketing_objectives": marketing.marketing_objectives,
            "marketing_budget": marketing.marketing_budget,
            "date_flexibility": marketing.date_flexibility,
            "has_special_guests": marketing.has_special_guests,
            "promotion_participants": marketing.promotion_participants,
            "lyrics": marketing.lyrics,
            "general_notes": marketing.general_notes,
            "additional_files": [f.model_dump(mode="python") for f in marketing.additional_files],
            "presskit_link": project.presskit_link,
            "tiktok_snippet": project.tiktok_snippet,
            "has_video_asset": project.has_video_asset,
        },

        # payload bruto canônico
        "payload": payload.model_dump(mode="python"),
    }

    try:
        submission_res = supabase.table("submissions").insert(submission_row).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create submission: {exc}")

    if not submission_res.data:
        raise HTTPException(status_code=500, detail="Failed to create submission")

    created_submission = submission_res.data[0]

    # tracks
    track_rows: List[Dict[str, Any]] = []
    for track in payload.tracks:
        track_rows.append(
            {
                "draft_token": payload.draft_token,
                "submission_id": submission_id,
                "order_number": track.order_number,
                "title": track.title,
                "isrc": track.isrc_code or None,
                "artists": track.primary_artists,
                "feats": track.featured_artists or None,
                "author": track.authors,
                "lyrics": track.lyrics or None,
                "explicit": _bool_from_yes_no(track.explicit_content),
                "audio_path": track.audio_file.storage_path if track.audio_file else None,
                "created_at": now_iso,
            }
        )

    created_tracks: List[Dict[str, Any]] = []
    if track_rows:
        try:
            tracks_res = supabase.table("tracks").insert(track_rows).execute()
            created_tracks = tracks_res.data or []
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Submission created but failed to create tracks: {exc}"
            )

    return {
        "ok": True,
        "submission_id": created_submission.get("id"),
        "client_slug": client_slug,
        "draft_token": payload.draft_token,
        "edit_token": edit_token,
        "tracks_created": len(created_tracks),
        "message": "Submission created successfully.",
    }