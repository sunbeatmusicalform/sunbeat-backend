from __future__ import annotations

import mimetypes
import hashlib
import re
import time
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import supabase
from app.services.asset_retention import assert_asset_not_expired, register_asset


router = APIRouter(tags=["file_uploads"])

UploadKind = Literal["cover", "audio", "asset"]

UPLOAD_RULES = {
    "cover": {
        "folder": "cover",
        "max_size": 100 * 1024 * 1024,
        "extensions": {".jpg", ".jpeg", ".png", ".tif", ".tiff"},
        "mime_types": {"image/jpeg", "image/png", "image/tiff", "image/x-tiff"},
    },
    "audio": {
        "folder": "audio",
        "max_size": 100 * 1024 * 1024,
        "extensions": {".wav", ".flac"},
        "mime_types": {"audio/wav", "audio/x-wav", "audio/flac", "audio/x-flac"},
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


class SignedUploadRequest(BaseModel):
    kind: UploadKind
    file_name: str
    mime_type: str = ""
    file_size: int = 0
    workspace_slug: str
    draft_token: str
    track_local_id: str = ""


def _validate_upload_metadata(kind: UploadKind, file_name: str, mime_type: str, file_size: int) -> tuple[dict, str]:
    rules = UPLOAD_RULES[kind]
    safe_name = _safe_file_name(file_name)
    extension = PurePosixPath(safe_name).suffix.lower()
    normalized_mime = mime_type.lower()
    if extension not in rules["extensions"]:
        raise HTTPException(status_code=400, detail="Formato de arquivo não permitido.")
    if normalized_mime and normalized_mime not in rules["mime_types"]:
        raise HTTPException(status_code=400, detail="Tipo MIME não permitido.")
    if file_size < 0 or file_size > int(rules["max_size"]):
        raise HTTPException(status_code=413, detail="Arquivo excede o limite permitido.")
    return rules, safe_name


def _storage_path_for(
    *,
    kind: UploadKind,
    file_name: str,
    workspace_slug: str,
    draft_token: str,
    track_local_id: str,
) -> str:
    rules = UPLOAD_RULES[kind]
    path_parts = [
        _safe_segment(workspace_slug),
        "drafts",
        _safe_segment(draft_token),
        str(rules["folder"]),
    ]
    if kind == "audio" and track_local_id:
        path_parts.append(_safe_segment(track_local_id))
    path_parts.append(f"{int(time.time() * 1000)}-{file_name}")
    return "/".join(path_parts)


def _file_ref(request: Request, *, bucket: str, storage_path: str, file_name: str, mime_type: str, file_size: int) -> dict:
    origin = str(request.base_url).rstrip("/")
    encoded = _encoded_path(storage_path)
    return {
        "file_name": file_name,
        "storage_bucket": bucket,
        "storage_path": storage_path,
        "public_url": f"{origin}/files/{quote(bucket, safe='')}/{encoded}",
        "download_url": f"{origin}/files/{quote(bucket, safe='')}/download/{encoded}",
        "mime_type": mime_type or "application/octet-stream",
        "size_bytes": file_size,
    }


@router.post("/uploads/sign")
async def sign_upload(request: Request, payload: SignedUploadRequest) -> dict:
    _, file_name = _validate_upload_metadata(
        payload.kind, payload.file_name, payload.mime_type, payload.file_size
    )
    storage_path = _storage_path_for(
        kind=payload.kind,
        file_name=file_name,
        workspace_slug=payload.workspace_slug,
        draft_token=payload.draft_token,
        track_local_id=payload.track_local_id,
    )
    bucket = _bucket_for(payload.kind)
    try:
        signed = await run_in_threadpool(
            supabase.storage.from_(bucket).create_signed_upload_url,
            storage_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao preparar upload: {exc}") from exc
    register_asset(
        workspace_slug=payload.workspace_slug,
        draft_token=payload.draft_token,
        storage_bucket=bucket,
        storage_path=storage_path,
        file_name=payload.file_name,
        mime_type=payload.mime_type,
        size_bytes=payload.file_size,
        status="pending_upload",
    )
    return {
        "ok": True,
        "signed_upload_url": signed["signed_url"],
        "file": _file_ref(
            request,
            bucket=bucket,
            storage_path=storage_path,
            file_name=payload.file_name,
            mime_type=payload.mime_type,
            file_size=payload.file_size,
        ),
    }


@router.post("/uploads")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    kind: UploadKind = Form(...),
    workspace_slug: str = Form(...),
    draft_token: str = Form(...),
    track_local_id: str = Form(""),
) -> dict:
    rules, file_name = _validate_upload_metadata(
        kind, file.filename or "arquivo", file.content_type or "", 0
    )
    mime_type = (file.content_type or "").lower()

    content = await file.read(int(rules["max_size"]) + 1)
    if len(content) > int(rules["max_size"]):
        raise HTTPException(status_code=413, detail="Arquivo excede o limite permitido.")

    storage_path = _storage_path_for(
        kind=kind,
        file_name=file_name,
        workspace_slug=workspace_slug,
        draft_token=draft_token,
        track_local_id=track_local_id,
    )
    bucket = _bucket_for(kind)

    try:
        await run_in_threadpool(
            supabase.storage.from_(bucket).upload,
            storage_path,
            content,
            {"content-type": mime_type or "application/octet-stream", "upsert": "false"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao armazenar arquivo: {exc}") from exc

    try:
        register_asset(
            workspace_slug=workspace_slug,
            draft_token=draft_token,
            storage_bucket=bucket,
            storage_path=storage_path,
            file_name=file.filename or file_name,
            mime_type=mime_type,
            size_bytes=len(content),
            status="uploaded",
            content_sha256=hashlib.sha256(content).hexdigest(),
        )
    except HTTPException:
        # Do not leave an untracked Free asset behind when the registry fails.
        try:
            await run_in_threadpool(supabase.storage.from_(bucket).remove, [storage_path])
        except Exception:
            pass
        raise

    return {
        "ok": True,
        **_file_ref(
            request,
            bucket=bucket,
            storage_path=storage_path,
            file_name=file.filename or file_name,
            mime_type=mime_type,
            file_size=len(content),
        ),
    }


def _download_storage_file(bucket: str, storage_path: str) -> bytes:
    if bucket not in _allowed_buckets():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    if not storage_path or ".." in storage_path.split("/"):
        raise HTTPException(status_code=400, detail="Caminho de arquivo inválido.")
    assert_asset_not_expired(storage_bucket=bucket, storage_path=storage_path)
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
