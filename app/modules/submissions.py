from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from app.core.database import supabase


# -----------------------------
# ROUTER
# -----------------------------
router = APIRouter()


# -----------------------------
# MODELS
# -----------------------------
class DraftCreate(BaseModel):
    artist_name: Optional[str] = None
    email: Optional[str] = None
    track_title: Optional[str] = None
    genre: Optional[str] = None
    lyrics: Optional[str] = None


class DraftUpdate(BaseModel):
    artist_name: Optional[str] = None
    email: Optional[str] = None
    track_title: Optional[str] = None
    genre: Optional[str] = None
    lyrics: Optional[str] = None


class TrackUpsert(BaseModel):
    order_number: int = Field(..., ge=1)
    title: Optional[str] = None
    isrc: Optional[str] = None
    artists: Optional[str] = None
    feats: Optional[str] = None
    authors: Optional[str] = None
    lyrics: Optional[str] = None
    explicit: Optional[bool] = None


# -----------------------------
# HELPERS / CONSTANTS
# -----------------------------
COVER_BUCKET = "sunbeat-covers"
AUDIO_BUCKET = "sunbeat-audio"

MAX_COVER_MB = 15
MAX_AUDIO_MB = 200


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(name: str) -> str:
    name = name or "file"
    name = re.sub(r"[^\w\-.]+", "_", name.strip())
    return name[:180] if len(name) > 180 else name


def _read_and_validate(file: UploadFile, max_mb: int) -> bytes:
    content = file.file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > max_mb:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({size_mb:.1f}MB). Limit: {max_mb}MB",
        )
    return content


# -----------------------------
# HEALTHCHECK (SUPABASE)
# -----------------------------
@router.get("/test-supabase")
def test_supabase():
    """
    Teste robusto que NÃO depende de UUID fixo.
    Se a tabela 'submissions' existir, retorna ok.
    """
    try:
        res = supabase.table("submissions").select("id").limit(1).execute()
        return {"ok": True, "table": "submissions", "rows_sampled": len(res.data or [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase test failed: {str(e)}")


# -----------------------------
# DRAFT ENDPOINTS
# -----------------------------
@router.post("/")
def create_draft(payload: DraftCreate):
    draft_token = str(uuid.uuid4())
    row = {
        "draft_token": draft_token,
        "artist_name": payload.artist_name,
        "email": payload.email,
        "track_title": payload.track_title,
        "genre": payload.genre,
        "lyrics": payload.lyrics,
        "status": "draft",
        "version": 1,
        "is_update": False,
        "parent_submission_id": None,
        "created_at": _utc_iso(),
    }

    res = supabase.table("submissions").insert(row).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to create draft")

    return {"draft_token": draft_token, "status": "draft_created"}


@router.patch("/{draft_token}")
def update_draft(draft_token: str, payload: DraftUpdate):
    # pega a última versão e cria uma NOVA versão draft (INSERT-only)
    last = (
        supabase.table("submissions")
        .select("*")
        .eq("draft_token", draft_token)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    if not last.data:
        raise HTTPException(status_code=404, detail="Draft token not found")

    last_row = last.data[0]
    new_version = int(last_row.get("version") or 0) + 1

    row = {
        "draft_token": draft_token,
        "artist_name": payload.artist_name if payload.artist_name is not None else last_row.get("artist_name"),
        "email": payload.email if payload.email is not None else last_row.get("email"),
        "track_title": payload.track_title if payload.track_title is not None else last_row.get("track_title"),
        "genre": payload.genre if payload.genre is not None else last_row.get("genre"),
        "lyrics": payload.lyrics if payload.lyrics is not None else last_row.get("lyrics"),
        "cover_path": last_row.get("cover_path"),
        "status": "draft",
        "version": new_version,
        "is_update": False,
        "parent_submission_id": last_row.get("parent_submission_id"),
        "created_at": _utc_iso(),
    }

    ins = supabase.table("submissions").insert(row).execute()
    if not ins.data:
        raise HTTPException(status_code=500, detail="Failed to update draft (insert new version)")

    return {"status": "draft_updated", "version": new_version}


@router.get("/{draft_token}")
def get_latest(draft_token: str):
    res = (
        supabase.table("submissions")
        .select("*")
        .eq("draft_token", draft_token)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=404, detail="Draft token not found")

    return res.data[0]


# -----------------------------
# TRACKS (DRAFT)
# -----------------------------
@router.get("/{draft_token}/tracks")
def list_tracks(draft_token: str):
    res = (
        supabase.table("tracks")
        .select("*")
        .eq("draft_token", draft_token)
        .is_("submission_id", "null")  # tracks ainda não “snapshoteadas”
        .order("order_number", desc=False)
        .execute()
    )
    return {"draft_token": draft_token, "tracks": res.data or []}


@router.post("/{draft_token}/tracks")
def upsert_track(draft_token: str, payload: TrackUpsert):
    # garante que existe draft (em qualquer versão)
    chk = (
        supabase.table("submissions")
        .select("id")
        .eq("draft_token", draft_token)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    if not chk.data:
        raise HTTPException(status_code=404, detail="Draft token not found")

    # procura track draft existente (submission_id NULL)
    existing = (
        supabase.table("tracks")
        .select("id")
        .eq("draft_token", draft_token)
        .eq("order_number", payload.order_number)
        .is_("submission_id", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    data = {
        "title": payload.title,
        "isrc": payload.isrc,
        "artists": payload.artists,
        "feats": payload.feats,
        "authors": payload.authors,
        "lyrics": payload.lyrics,
        "explicit": payload.explicit,
    }

    # remove None pra não sobrescrever com null sem querer
    data = {k: v for k, v in data.items() if v is not None}

    if existing.data:
        track_id = existing.data[0]["id"]
        upd = supabase.table("tracks").update(data).eq("id", track_id).execute()
        if not upd.data:
            raise HTTPException(status_code=500, detail="Failed to update track")
        return {"status": "track_updated", "track_id": track_id, "order_number": payload.order_number}

    ins_payload = {
        "draft_token": draft_token,
        "submission_id": None,
        "order_number": payload.order_number,
        **data,
        "created_at": _utc_iso(),
    }
    ins = supabase.table("tracks").insert(ins_payload).execute()
    if not ins.data:
        raise HTTPException(status_code=500, detail="Failed to create track")
    return {"status": "track_created", "track_id": ins.data[0]["id"], "order_number": payload.order_number}


@router.delete("/{draft_token}/tracks/{order_number}")
def delete_track(draft_token: str, order_number: int):
    # remove só track draft (submission_id null)
    res = (
        supabase.table("tracks")
        .delete()
        .eq("draft_token", draft_token)
        .eq("order_number", order_number)
        .is_("submission_id", "null")
        .execute()
    )
    return {"status": "track_deleted", "order_number": order_number, "deleted": len(res.data or [])}


# -----------------------------
# UPLOADS
# -----------------------------
@router.post("/{draft_token}/upload/cover")
def upload_cover(draft_token: str, file: UploadFile = File(...)):
    # garante draft (status draft)
    chk = (
        supabase.table("submissions")
        .select("id")
        .eq("draft_token", draft_token)
        .eq("status", "draft")
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    if not chk.data:
        raise HTTPException(status_code=404, detail="Draft not found (or not in draft status)")

    content = _read_and_validate(file, MAX_COVER_MB)
    filename = _safe_filename(file.filename)
    obj_path = f"{draft_token}/cover/{uuid.uuid4()}_{filename}"

    try:
        supabase.storage.from_(COVER_BUCKET).upload(
            obj_path,
            content,
            file_options={"content-type": file.content_type or "application/octet-stream"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {str(e)}")

    # grava no draft (update permitido)
    up = (
        supabase.table("submissions")
        .update({"cover_path": obj_path})
        .eq("draft_token", draft_token)
        .eq("status", "draft")
        .execute()
    )
    if not up.data:
        raise HTTPException(status_code=500, detail="Uploaded cover, but failed to update draft cover_path")

    return {"status": "cover_uploaded", "cover_path": obj_path}


def _upsert_track_audio(draft_token: str, order_number: int, audio_path: str):
    """
    Garante que exista um registro em tracks para esse draft_token + order_number (submission_id NULL).
    Se existir, atualiza; se não, cria.
    """
    existing = (
        supabase.table("tracks")
        .select("id")
        .eq("draft_token", draft_token)
        .eq("order_number", order_number)
        .is_("submission_id", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if existing.data:
        track_id = existing.data[0]["id"]
        upd = supabase.table("tracks").update({"audio_path": audio_path}).eq("id", track_id).execute()
        if not upd.data:
            raise HTTPException(status_code=500, detail="Failed to update track audio_path")
        return track_id

    ins = (
        supabase.table("tracks")
        .insert(
            {
                "draft_token": draft_token,
                "submission_id": None,
                "order_number": order_number,
                "audio_path": audio_path,
                "created_at": _utc_iso(),
            }
        )
        .execute()
    )
    if not ins.data:
        raise HTTPException(status_code=500, detail="Failed to insert track row for audio")
    return ins.data[0]["id"]


@router.post("/{draft_token}/upload/audio/{order_number}")
def upload_audio(draft_token: str, order_number: int, file: UploadFile = File(...)):
    """
    Upload de ÁUDIO (1 por faixa).
    Salva no bucket sunbeat-audio e grava tracks.audio_path.
    """
    # garante que existe draft ativo
    chk = (
        supabase.table("submissions")
        .select("id")
        .eq("draft_token", draft_token)
        .eq("status", "draft")
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    if not chk.data:
        raise HTTPException(status_code=404, detail="Draft not found (or not in draft status)")

    content = _read_and_validate(file, MAX_AUDIO_MB)
    filename = _safe_filename(file.filename)
    obj_path = f"{draft_token}/audio/track_{order_number}/{uuid.uuid4()}_{filename}"

    try:
        supabase.storage.from_(AUDIO_BUCKET).upload(
            obj_path,
            content,
            file_options={"content-type": file.content_type or "application/octet-stream"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {str(e)}")

    track_id = _upsert_track_audio(draft_token, order_number, obj_path)

    return {"status": "audio_uploaded", "order_number": order_number, "track_id": track_id, "audio_path": obj_path}


# -----------------------------
# SUBMIT (INSERT-ONLY + SNAPSHOT TRACKS)
# -----------------------------
@router.post("/submit/{draft_token}")
def submit_draft(draft_token: str):
    """
    SUBMIT com versionamento real (INSERT-only):
    - Busca a última versão (qualquer status) para copiar os campos.
    - Calcula next_version baseado na maior versão existente.
    - is_update=True apenas se já existe 'submitted' antes.
    - parent_submission_id:
        - primeiro submit: NULL
        - próximos submits: id do primeiro 'submitted' (âncora)
    - Snapshot das tracks:
        - pega tracks draft (submission_id NULL)
        - insere cópias com submission_id = id do novo submitted
    """

    # última versão
    last_res = (
        supabase.table("submissions")
        .select("*")
        .eq("draft_token", draft_token)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    if not last_res.data:
        raise HTTPException(status_code=404, detail="Draft token not found")

    last_row = last_res.data[0]
    last_version = int(last_row.get("version") or 0)

    # já existe submitted?
    submitted_latest = (
        supabase.table("submissions")
        .select("id, version, parent_submission_id")
        .eq("draft_token", draft_token)
        .eq("status", "submitted")
        .order("version", desc=True)
        .limit(1)
        .execute()
    )

    has_submitted = bool(submitted_latest.data)

    if not has_submitted:
        is_update = False
        parent_id = None
    else:
        is_update = True
        any_parent = submitted_latest.data[0].get("parent_submission_id")
        if any_parent:
            parent_id = any_parent
        else:
            first_submitted = (
                supabase.table("submissions")
                .select("id")
                .eq("draft_token", draft_token)
                .eq("status", "submitted")
                .order("version", desc=False)
                .limit(1)
                .execute()
            )
            parent_id = first_submitted.data[0]["id"] if first_submitted.data else None

    new_version = last_version + 1

    insert_payload = {
        "draft_token": draft_token,
        "artist_name": last_row.get("artist_name"),
        "email": last_row.get("email"),
        "track_title": last_row.get("track_title"),
        "genre": last_row.get("genre"),
        "lyrics": last_row.get("lyrics"),
        "cover_path": last_row.get("cover_path"),
        "ip_address": last_row.get("ip_address"),
        "user_agent": last_row.get("user_agent"),
        "status": "submitted",
        "version": new_version,
        "is_update": is_update,
        "parent_submission_id": parent_id,
        "submitted_at": _utc_iso(),
    }

    ins = supabase.table("submissions").insert(insert_payload).execute()
    if not getattr(ins, "data", None):
        raise HTTPException(status_code=500, detail="Failed to submit (insert)")

    new_submission_id = ins.data[0]["id"]

    # snapshot tracks (draft -> submitted)
    draft_tracks = (
        supabase.table("tracks")
        .select("*")
        .eq("draft_token", draft_token)
        .is_("submission_id", "null")
        .order("order_number", desc=False)
        .execute()
    )

    if draft_tracks.data:
        to_insert = []
        for t in draft_tracks.data:
            to_insert.append(
                {
                    "draft_token": draft_token,
                    "submission_id": new_submission_id,
                    "order_number": t.get("order_number"),
                    "title": t.get("title"),
                    "isrc": t.get("isrc"),
                    "artists": t.get("artists"),
                    "feats": t.get("feats"),
                    "authors": t.get("authors"),
                    "lyrics": t.get("lyrics"),
                    "explicit": t.get("explicit"),
                    "audio_path": t.get("audio_path"),
                    "created_at": _utc_iso(),
                }
            )

        tr_ins = supabase.table("tracks").insert(to_insert).execute()
        if not getattr(tr_ins, "data", None):
            raise HTTPException(status_code=500, detail="Submitted, but failed to snapshot tracks")

    return {
        "status": "submitted",
        "version": new_version,
        "is_update": is_update,
        "parent_submission_id": parent_id,
        "submission_id": new_submission_id,
        "tracks_snapshotted": len(draft_tracks.data or []),
    }