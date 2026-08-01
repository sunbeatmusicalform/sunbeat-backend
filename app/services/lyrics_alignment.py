from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, BinaryIO, Dict, List

import httpx

from app.core.config import settings


class LyricsAlignmentError(RuntimeError):
    pass


def lyrics_api_configured() -> bool:
    return bool(settings.GEMINI_LYRICS_API_KEY)


def _api_key() -> str:
    if not settings.GEMINI_LYRICS_API_KEY:
        raise LyricsAlignmentError("Lyrics alignment is not configured")
    return settings.GEMINI_LYRICS_API_KEY


def _base_url() -> str:
    return str(settings.GEMINI_API_BASE_URL or "https://generativelanguage.googleapis.com").rstrip("/")


def _timeout() -> float:
    return max(30.0, float(settings.GEMINI_LYRICS_TIMEOUT_SECONDS or 180))


def _lyrics_lines(lyrics: str) -> List[Dict[str, str]]:
    lines: List[Dict[str, str]] = []
    for raw_line in lyrics.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        text = raw_line.strip()
        if not text:
            continue
        lines.append({"id": f"line-{len(lines) + 1:03d}", "text": text})
    return lines


def _prompt(lines: List[Dict[str, str]]) -> str:
    numbered = "\n".join(f'{line["id"]}: {line["text"]}' for line in lines)
    return f"""You are aligning approved song lyrics to the supplied final master audio.

The provided lyrics are authoritative. Never rewrite, correct, translate, merge, split, or invent lyric lines.
Return exactly one result for every supplied line_id, in the same order. Repeated chorus lines are separate occurrences.
For sung/spoken lines, return the audible start and end in integer milliseconds.
For headings such as [Verse], [Chorus], or [Refrão], use status \"section\" and null timestamps.
If a line cannot be heard confidently, use status \"unmatched\" and null timestamps.
Confidence must be between 0 and 1. Timestamps must follow the audio timeline and may not overlap backwards.

Approved lines:
{numbered}
"""


def _response_schema() -> Dict[str, Any]:
    return {
        "type": "OBJECT",
        "required": ["lines"],
        "properties": {
            "lines": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "required": ["line_id", "status", "confidence"],
                    "properties": {
                        "line_id": {"type": "STRING"},
                        "status": {"type": "STRING", "enum": ["timed", "section", "unmatched"]},
                        "start_ms": {"type": "INTEGER", "nullable": True},
                        "end_ms": {"type": "INTEGER", "nullable": True},
                        "confidence": {"type": "NUMBER"},
                    },
                },
            },
            "duration_ms": {"type": "INTEGER", "nullable": True},
        },
    }


def _json_response(response: httpx.Response, *, stage: str) -> Dict[str, Any]:
    try:
        payload = response.json()
    except Exception as exc:
        raise LyricsAlignmentError(f"Gemini returned an invalid response during {stage}") from exc
    if response.status_code >= 400:
        raise LyricsAlignmentError(f"Gemini rejected the request during {stage}")
    if not isinstance(payload, dict):
        raise LyricsAlignmentError(f"Gemini returned an invalid response during {stage}")
    return payload


def _upload_file(
    client: httpx.Client,
    *,
    audio_file: BinaryIO,
    filename: str,
    mime_type: str,
    size_bytes: int,
) -> Dict[str, Any]:
    init = client.post(
        f"{_base_url()}/upload/v1beta/files",
        params={"key": _api_key()},
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size_bytes),
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "Content-Type": "application/json",
        },
        json={"file": {"display_name": Path(filename).name[:120]}},
    )
    if init.status_code >= 400:
        _json_response(init, stage="upload initialization")
    upload_url = init.headers.get("x-goog-upload-url")
    if not upload_url:
        raise LyricsAlignmentError("Gemini did not provide an upload URL")

    audio_file.seek(0)
    uploaded = client.post(
        upload_url,
        headers={
            "Content-Length": str(size_bytes),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
            "Content-Type": mime_type,
        },
        content=audio_file,
    )
    payload = _json_response(uploaded, stage="audio upload")
    file_payload = payload.get("file") if isinstance(payload.get("file"), dict) else payload
    if not file_payload.get("name") or not file_payload.get("uri"):
        raise LyricsAlignmentError("Gemini upload response did not include the file reference")
    return file_payload


def _wait_until_active(client: httpx.Client, file_payload: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(file_payload)
    deadline = time.monotonic() + min(_timeout(), 120.0)
    while str(current.get("state") or "ACTIVE").upper() == "PROCESSING":
        if time.monotonic() >= deadline:
            raise LyricsAlignmentError("Gemini took too long to process the audio")
        time.sleep(1.0)
        response = client.get(f"{_base_url()}/v1beta/{current['name']}", params={"key": _api_key()})
        current = _json_response(response, stage="audio processing")
    if str(current.get("state") or "ACTIVE").upper() not in {"ACTIVE", "STATE_UNSPECIFIED"}:
        raise LyricsAlignmentError("Gemini could not process the audio")
    return current


def _extract_model_json(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        text = "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
        result = json.loads(text)
    except Exception as exc:
        raise LyricsAlignmentError("Gemini did not return valid timestamp data") from exc
    if not isinstance(result, dict) or not isinstance(result.get("lines"), list):
        raise LyricsAlignmentError("Gemini did not return timestamp lines")
    return result


def _normalize_result(model_result: Dict[str, Any], originals: List[Dict[str, str]]) -> Dict[str, Any]:
    raw_by_id = {
        str(item.get("line_id")): item
        for item in model_result.get("lines", [])
        if isinstance(item, dict) and item.get("line_id")
    }
    normalized: List[Dict[str, Any]] = []
    previous_start = 0
    for original in originals:
        raw = raw_by_id.get(original["id"], {})
        status = str(raw.get("status") or "unmatched")
        start = raw.get("start_ms")
        end = raw.get("end_ms")
        try:
            start = max(0, int(start)) if start is not None else None
            end = max(0, int(end)) if end is not None else None
        except (TypeError, ValueError):
            start = end = None
        if status != "timed" or start is None or end is None or end <= start or start < previous_start:
            status = "section" if status == "section" else "unmatched"
            start = end = None
        else:
            previous_start = start
        try:
            confidence = min(1.0, max(0.0, float(raw.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        normalized.append(
            {
                "id": original["id"],
                "text": original["text"],
                "start_ms": start,
                "end_ms": end,
                "confidence": confidence,
                "status": status,
                "needs_review": status != "timed" or confidence < 0.75,
            }
        )
    return {
        "lines": normalized,
        "duration_ms": model_result.get("duration_ms"),
    }


def align_lyrics_with_gemini(
    *,
    audio_file: BinaryIO,
    filename: str,
    mime_type: str,
    size_bytes: int,
    lyrics: str,
) -> Dict[str, Any]:
    originals = _lyrics_lines(lyrics)
    if not originals:
        raise LyricsAlignmentError("No lyric lines were provided")

    uploaded: Dict[str, Any] | None = None
    with httpx.Client(timeout=httpx.Timeout(_timeout(), connect=20.0)) as client:
        try:
            uploaded = _upload_file(
                client,
                audio_file=audio_file,
                filename=filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
            )
            uploaded = _wait_until_active(client, uploaded)
            response = client.post(
                f"{_base_url()}/v1beta/models/{settings.GEMINI_LYRICS_MODEL}:generateContent",
                params={"key": _api_key()},
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {"text": _prompt(originals)},
                                {"file_data": {"mime_type": mime_type, "file_uri": uploaded["uri"]}},
                            ],
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0,
                        "responseMimeType": "application/json",
                        "responseSchema": _response_schema(),
                    },
                },
            )
            result = _normalize_result(
                _extract_model_json(_json_response(response, stage="lyrics alignment")),
                originals,
            )
            return {
                "ok": True,
                "provider": "gemini",
                "model": settings.GEMINI_LYRICS_MODEL,
                **result,
            }
        finally:
            if uploaded and uploaded.get("name"):
                try:
                    client.delete(f"{_base_url()}/v1beta/{uploaded['name']}", params={"key": _api_key()})
                except Exception:
                    pass
