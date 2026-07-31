from __future__ import annotations

import mimetypes
import re
import time
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from app.core.config import settings
from app.core.database import supabase


router = APIRouter(tags=["file_uploads"])

UploadKind = Literal["cover", "audio", "asset"]

UPLOAD_RULES = {
    "cover": {
        "folder": "cover",
        "max_size": 50 * 1024 * 1024,
        "extensions": {".jpg", ".jpeg", ".png"},
        "mime_types": {"image/jpeg", "image/png"},
    },
    "audio": {
        "folder": "audio",
        "max_size": 100 * 1024 * 1024,
        "extensions": {".wav", ".mp3"},
        "mime_types": {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3"},
    },
    "asset": {
        "folder": "assets",
        "max_size": 50 * 1024 * 1024,
        "extensions": {".jpg", ".jpeg", ".png", ".pdf", ".zip"},
        "mime_types": {
            "image/jpeg",
            "image/png",
            "application/pdf",
            "application/zip",
            "application/x-zip-compressed",
        },
    },
}


def _safe_segment(value: str, fallback: str = "unknown") -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-")
    return safe or fallback


def _safe_file_name(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", PurePosixPath(value).name.strip())
    return safe or "arquivo"


def _bucket_for(kind: UploadKind) -> str:
    if kind == "cover":
        return getattr(settings, "SUPABASE_COVERS_BUCKET", None) or "sunbeat-covers"
    if kind == "audio":
        return getattr(settings, "SUPABASE_AUDIO_BUCKET", None) or "sunbeat-audio"
    return (
        getattr(settings, "SUPABASE_ASSETS_BUCKET", None)
        or getattr(settings, "SUPABASE_COVERS_BUCKET", None)
        or "sunbeat-covers"
    )


def _allowed_buckets() -> set[str]:
    return {_bucket_for("cover"), _bucket_for("audio"), _bucket_for("asset")}


def _encoded_path(path: str) -> str:
    return "/".join(quote(part, safe="") for part in path.split("/") if part)


@router.post("/uploads")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    kind: UploadKind = Form(...),
    workspace_slug: str = Form(...),
    draft_token: str = Form(...),
    track_local_id: str = Form(""),
) -> dict:
    rules = UPLOAD_RULES[kind]
    file_name = _safe_file_name(file.filename or "arquivo")
    extension = PurePosixPath(file_name).suffix.lower()
    mime_type = (file.content_type or "").lower()

    if extension not in rules["extensions"]:
        raise HTTPException(status_code=400, detail="Formato de arquivo não permitido.")
    if mime_type and mime_type not in rules["mime_types"]:
        raise HTTPException(status_code=400, detail="Tipo MIME não permitido.")

    content = await file.read(int(rules["max_size"]) + 1)
    if len(content) > int(rules["max_size"]):
        raise HTTPException(status_code=413, detail="Arquivo excede o limite permitido.")

    path_parts = [
        _safe_segment(workspace_slug),
        "drafts",
        _safe_segment(draft_token),
        str(rules["folder"]),
    ]
    if kind == "audio" and track_local_id:
        path_parts.append(_safe_segment(track_local_id))
    path_parts.append(f"{int(time.time() * 1000)}-{file_name}")
    storage_path = "/".join(path_parts)
    bucket = _bucket_for(kind)

    try:
        supabase.storage.from_(bucket).upload(
            storage_path,
            content,
            {"content-type": mime_type or "application/octet-stream", "upsert": "false"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao armazenar arquivo: {exc}") from exc

    origin = str(request.base_url).rstrip("/")
    encoded = _encoded_path(storage_path)
    return {
        "ok": True,
        "file_name": file.filename or file_name,
        "storage_bucket": bucket,
        "storage_path": storage_path,
        "public_url": f"{origin}/files/{quote(bucket, safe='')}/{encoded}",
        "download_url": f"{origin}/files/{quote(bucket, safe='')}/download/{encoded}",
        "mime_type": mime_type or "application/octet-stream",
        "size_bytes": len(content),
    }


def _download_storage_file(bucket: str, storage_path: str) -> bytes:
    if bucket not in _allowed_buckets():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    if not storage_path or ".." in storage_path.split("/"):
        raise HTTPException(status_code=400, detail="Caminho de arquivo inválido.")
    try:
        return supabase.storage.from_(bucket).download(storage_path)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.") from exc


@router.get("/files/{bucket}/download/{storage_path:path}")
def download_file(bucket: str, storage_path: str) -> Response:
    content = _download_storage_file(bucket, storage_path)
    file_name = PurePosixPath(storage_path).name
    mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"},
    )


@router.get("/files/{bucket}/{storage_path:path}")
def preview_file(bucket: str, storage_path: str) -> Response:
    content = _download_storage_file(bucket, storage_path)
    mime_type = mimetypes.guess_type(storage_path)[0] or "application/octet-stream"
    return Response(content=content, media_type=mime_type)
