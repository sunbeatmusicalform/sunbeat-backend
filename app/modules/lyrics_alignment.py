from __future__ import annotations

import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from typing import Deque, Dict

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.core.config import settings
from app.services.lyrics_alignment import LyricsAlignmentError, align_lyrics_with_gemini, lyrics_api_configured

router = APIRouter(prefix="/lyrics", tags=["lyrics"])

ALLOWED_EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg"}
ALLOWED_MIME_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/flac",
    "audio/x-flac",
    "audio/mpeg",
    "audio/mp4",
    "audio/aac",
    "audio/ogg",
}
RATE_LIMIT_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 15 * 60
_requests_by_client: Dict[str, Deque[float]] = defaultdict(deque)
_rate_limit_lock = Lock()


def _allowed_workspaces() -> set[str]:
    return {
        value.strip().lower()
        for value in str(settings.GEMINI_LYRICS_WORKSPACES or "atabaque").split(",")
        if value.strip()
    }


def _client_key(request: Request, workspace_slug: str) -> str:
    address = request.headers.get("fly-client-ip") or request.headers.get("x-forwarded-for")
    if address:
        address = address.split(",", 1)[0].strip()
    if not address and request.client:
        address = request.client.host
    return f"{workspace_slug}:{address or 'unknown'}"


def _enforce_rate_limit(key: str) -> None:
    now = time.monotonic()
    with _rate_limit_lock:
        bucket = _requests_by_client[key]
        while bucket and bucket[0] <= now - RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_REQUESTS:
            raise HTTPException(status_code=429, detail="Limite temporário de sincronizações atingido. Tente novamente em alguns minutos.")
        bucket.append(now)


@router.post("/align")
def align_lyrics(
    request: Request,
    workspace_slug: str = Form(...),
    lyrics: str = Form(...),
    consent_ai_processing: bool = Form(False),
    audio: UploadFile = File(...),
):
    workspace = workspace_slug.strip().lower()
    if workspace not in _allowed_workspaces():
        raise HTTPException(status_code=403, detail="Sincronização não habilitada para este workspace.")
    if not lyrics_api_configured():
        raise HTTPException(status_code=503, detail="Sincronização de letra temporariamente indisponível.")
    if not consent_ai_processing:
        raise HTTPException(status_code=400, detail="Confirme o processamento do áudio e da letra por IA.")
    cleaned_lyrics = lyrics.strip()
    if len(cleaned_lyrics) < 3 or len(cleaned_lyrics) > 50_000:
        raise HTTPException(status_code=400, detail="A letra deve ter entre 3 e 50.000 caracteres.")

    filename = Path(audio.filename or "audio").name
    extension = Path(filename).suffix.lower()
    mime_type = (audio.content_type or "").lower()
    if extension not in ALLOWED_EXTENSIONS or mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=415, detail="Use áudio WAV, FLAC, MP3, M4A, AAC ou OGG.")

    audio.file.seek(0, 2)
    size_bytes = audio.file.tell()
    audio.file.seek(0)
    max_bytes = max(1, int(settings.GEMINI_LYRICS_MAX_AUDIO_MB or 250)) * 1024 * 1024
    if size_bytes <= 0 or size_bytes > max_bytes:
        raise HTTPException(status_code=413, detail=f"O áudio deve ter no máximo {settings.GEMINI_LYRICS_MAX_AUDIO_MB} MB.")

    _enforce_rate_limit(_client_key(request, workspace))
    try:
        return align_lyrics_with_gemini(
            audio_file=audio.file,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            lyrics=cleaned_lyrics,
        )
    except LyricsAlignmentError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Não foi possível sincronizar a letra agora.") from exc
