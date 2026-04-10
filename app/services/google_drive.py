from __future__ import annotations

import ast
import base64
import io
import json
import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from app.core.config import settings

logger = logging.getLogger("sunbeat.google_drive")
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
_DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_PROJECTS_FOLDER_NAME = "Projetos"


def _normalize_lookup_key(value: Optional[str]) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
    ascii_only = ascii_only.lower().strip()
    ascii_only = re.sub(r"[^a-z0-9]+", "-", ascii_only)
    return re.sub(r"-{2,}", "-", ascii_only).strip("-")


def _sanitize_name(value: Optional[str], fallback: str = "Sem Nome") -> str:
    text = (value or "").strip()
    text = re.sub(r'[\\/:*?"<>|#%{}~]+', " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text[:180] or fallback


def _extract_folder_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip()
    match = re.search(r"/folders/([a-zA-Z0-9_-]+)", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{10,}", text):
        return text
    return None


def _release_type_label(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower()
    if normalized == "single":
        return "Single"
    if normalized == "ep":
        return "EP"
    if normalized == "album":
        return "Album"
    return "Release"


def _build_release_folder_name(payload: Any) -> str:
    identification = getattr(payload, "identification", None)
    release_type = _release_type_label(getattr(identification, "release_type", None))
    project_title = _sanitize_name(getattr(identification, "project_title", None), "Projeto")
    return f"{release_type}_{project_title}"


def _get_first_primary_artist(payload: Any) -> Optional[str]:
    tracks = getattr(payload, "tracks", None) or []
    if not tracks:
        return None
    primary_artists = getattr(tracks[0], "primary_artists", None)
    if not primary_artists:
        return None
    return str(primary_artists).split(",")[0].strip() or None


def _build_secret_preview(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    preview = text[:180].replace("\n", "\\n")
    preview = re.sub(r"(private_key_id:)[^,}\s]+", r"\1***", preview, flags=re.IGNORECASE)
    preview = re.sub(
        r"(private_key\s*[:=]\s*)(.*?)(,|}|$)",
        r"\1***\3",
        preview,
        flags=re.IGNORECASE,
    )
    return preview


def _parse_service_account_json(raw_json: str) -> Dict[str, Any]:
    text = (raw_json or "").strip()
    if not text:
        raise RuntimeError("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON is not configured")

    last_error: Optional[Exception] = None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:
        last_error = exc

    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:
        last_error = exc

    try:
        decoded = base64.b64decode(text).decode("utf-8")
        parsed = json.loads(decoded)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:
        last_error = exc

    preview = _build_secret_preview(text)
    raise RuntimeError(
        "Could not parse GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON. "
        f"preview={preview!r}; error={last_error}"
    )


def _build_drive_service():
    raw_json = (settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON or "").strip()
    credentials_info = _parse_service_account_json(raw_json)

    logger.info(
        "Google Drive credentials loaded: project_id=%s client_email=%s",
        credentials_info.get("project_id"),
        credentials_info.get("client_email"),
    )

    credentials = Credentials.from_service_account_info(credentials_info, scopes=_DRIVE_SCOPES)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _drive_error_status(exc: Exception) -> Optional[int]:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _escape_drive_query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _get_folder_by_id(service: Any, folder_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not folder_id:
        return None

    try:
        folder = service.files().get(
            fileId=folder_id,
            fields="id,name,mimeType,trashed",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        status = _drive_error_status(exc)
        if status in {403, 404}:
            logger.warning(
                "Google Drive folder unavailable: folder_id=%s status=%s",
                folder_id,
                status,
            )
            return None
        raise

    if folder.get("trashed"):
        logger.warning("Google Drive folder is trashed: folder_id=%s", folder_id)
        return None

    if folder.get("mimeType") != _DRIVE_FOLDER_MIME_TYPE:
        logger.warning(
            "Google Drive item is not a folder: folder_id=%s mimeType=%s",
            folder_id,
            folder.get("mimeType"),
        )
        return None

    return {"id": folder["id"], "name": folder.get("name") or folder_id}


def _find_child_folder(service: Any, *, parent_id: str, name: str) -> Optional[Dict[str, Any]]:
    safe_name = _sanitize_name(name)
    escaped_name = _escape_drive_query_literal(safe_name)
    query = (
        f"'{parent_id}' in parents "
        f"and name = '{escaped_name}' "
        f"and mimeType = '{_DRIVE_FOLDER_MIME_TYPE}' and trashed = false"
    )
    existing = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id,name)",
        pageSize=1,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
    ).execute()
    files = existing.get("files", [])
    if files:
        folder = files[0]
        return {"id": folder["id"], "name": folder["name"], "created": False}

    return None


def _ensure_folder(service: Any, *, parent_id: str, name: str) -> Dict[str, Any]:
    existing_folder = _find_child_folder(service, parent_id=parent_id, name=name)
    if existing_folder:
        logger.info(
            "Google Drive folder reused: name=%s folder_id=%s parent_id=%s",
            existing_folder["name"],
            existing_folder["id"],
            parent_id,
        )
        return existing_folder

    metadata = {
        "name": _sanitize_name(name),
        "mimeType": _DRIVE_FOLDER_MIME_TYPE,
        "parents": [parent_id],
    }
    created = service.files().create(
        body=metadata,
        fields="id,name",
        supportsAllDrives=True,
    ).execute()
    logger.info(
        "Google Drive folder created: name=%s folder_id=%s parent_id=%s",
        created["name"],
        created["id"],
        parent_id,
    )
    return {"id": created["id"], "name": created["name"], "created": True}


def _pick_file_url(file_ref: Any) -> Optional[str]:
    if not file_ref:
        return None
    for attr in ("download_url", "public_url"):
        value = getattr(file_ref, attr, None)
        if value:
            return str(value)
    if isinstance(file_ref, dict):
        for key in ("download_url", "public_url"):
            value = file_ref.get(key)
            if value:
                return str(value)
    return None


def _get_file_name(file_ref: Any, fallback: str) -> str:
    if hasattr(file_ref, "file_name") and getattr(file_ref, "file_name", None):
        return _sanitize_name(getattr(file_ref, "file_name"), fallback)
    if isinstance(file_ref, dict) and file_ref.get("file_name"):
        return _sanitize_name(str(file_ref["file_name"]), fallback)
    return _sanitize_name(fallback, fallback)


def _get_mime_type(file_ref: Any) -> str:
    if hasattr(file_ref, "mime_type") and getattr(file_ref, "mime_type", None):
        return str(getattr(file_ref, "mime_type"))
    if isinstance(file_ref, dict) and file_ref.get("mime_type"):
        return str(file_ref["mime_type"])
    return "application/octet-stream"


def _get_storage_ref(file_ref: Any) -> str:
    if hasattr(file_ref, "storage_bucket") or hasattr(file_ref, "storage_path"):
        bucket = str(getattr(file_ref, "storage_bucket", "") or "").strip()
        path = str(getattr(file_ref, "storage_path", "") or "").strip()
    elif isinstance(file_ref, dict):
        bucket = str(file_ref.get("storage_bucket") or "").strip()
        path = str(file_ref.get("storage_path") or "").strip()
    else:
        bucket = ""
        path = ""

    if bucket and path:
        return f"{bucket}:{path}"
    return path or bucket or "unknown"


def _download_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    return response.content


def _upload_file_from_ref(service: Any, *, folder_id: str, file_ref: Any, fallback_name: str):
    file_url = _pick_file_url(file_ref)
    file_name = _get_file_name(file_ref, fallback_name)
    storage_ref = _get_storage_ref(file_ref)

    if not file_url:
        logger.warning(
            "Google Drive upload skipped: missing file URL folder_id=%s file_name=%s storage_ref=%s",
            folder_id,
            file_name,
            storage_ref,
        )
        return None

    mime_type = _get_mime_type(file_ref)
    logger.info(
        "Google Drive upload starting: folder_id=%s file_name=%s mime_type=%s storage_ref=%s source_url=%s",
        folder_id,
        file_name,
        mime_type,
        storage_ref,
        file_url,
    )
    content = _download_bytes(file_url)
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=False)
    metadata = {"name": file_name, "parents": [folder_id]}
    created = service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,mimeType,webViewLink",
        supportsAllDrives=True,
    ).execute()
    logger.info(
        "Google Drive file uploaded: folder_id=%s file_name=%s file_id=%s mime_type=%s",
        folder_id,
        created["name"],
        created["id"],
        created.get("mimeType"),
    )
    return created


def _airtable_headers() -> Dict[str, str]:
    api_key = (settings.AIRTABLE_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError("AIRTABLE_API_KEY is not configured")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _airtable_table_url() -> str:
    base_id = (settings.AIRTABLE_BASE_ID or "").strip()
    table_name = (settings.AIRTABLE_CLIENTS_TABLE or "").strip()
    if not base_id:
        raise RuntimeError("AIRTABLE_BASE_ID is not configured")
    if not table_name:
        raise RuntimeError("AIRTABLE_CLIENTS_TABLE is not configured")
    return f"https://api.airtable.com/v0/{base_id}/{requests.utils.quote(table_name, safe='')}"


def _airtable_list_records(params: Dict[str, str]) -> List[Dict[str, Any]]:
    url = _airtable_table_url()
    records: List[Dict[str, Any]] = []
    offset: Optional[str] = None

    while True:
        req_params = dict(params)
        if offset:
            req_params["offset"] = offset
        response = requests.get(url, headers=_airtable_headers(), params=req_params, timeout=60)
        response.raise_for_status()
        data = response.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break

    return records


def _airtable_update_record(record_id: str, fields: Dict[str, Any]) -> None:
    url = f"{_airtable_table_url()}/{record_id}"
    response = requests.patch(url, headers=_airtable_headers(), json={"fields": fields}, timeout=60)
    response.raise_for_status()
    logger.info("Airtable client record updated: %s fields=%s", record_id, list(fields.keys()))


def _get_field_value(fields: Dict[str, Any], field_name: str) -> Any:
    return fields.get(field_name)


def _resolve_matching_client_record(payload: Any) -> Optional[Dict[str, Any]]:
    artist = _get_first_primary_artist(payload)
    submitter_email = getattr(getattr(payload, "identification", None), "submitter_email", None)

    normalized_artist = _normalize_lookup_key(artist)
    normalized_submitter = _normalize_lookup_key(submitter_email)

    formula = (
        f"AND("
        f"{{{settings.AIRTABLE_CLIENT_STATUS_FIELD}}}='Ativo',"
        f"NOT({{{settings.AIRTABLE_CLIENT_NAME_FIELD}}}='')"
        f")"
    )
    records = _airtable_list_records({"filterByFormula": formula, "pageSize": "100"})
    matches: List[Dict[str, Any]] = []

    for record in records:
        fields = record.get("fields", {})
        client_name = str(_get_field_value(fields, settings.AIRTABLE_CLIENT_NAME_FIELD) or "")
        label_name = str(_get_field_value(fields, settings.AIRTABLE_CLIENT_LABEL_FIELD) or "")
        label_email = str(_get_field_value(fields, settings.AIRTABLE_CLIENT_LABEL_EMAIL_FIELD) or "")

        artist_match = bool(normalized_artist and _normalize_lookup_key(client_name) == normalized_artist)
        submitter_match = bool(
            normalized_submitter
            and (
                _normalize_lookup_key(label_email) == normalized_submitter
                or _normalize_lookup_key(label_name) == normalized_submitter
            )
        )

        if artist_match or submitter_match:
            matches.append(
                {
                    "record": record,
                    "artist_match": artist_match,
                    "submitter_match": submitter_match,
                    "client_name": client_name,
                    "label_name": label_name,
                    "label_email": label_email,
                }
            )

    both = [item for item in matches if item["artist_match"] and item["submitter_match"]]
    if both:
        return both[0]
    artist_only = [item for item in matches if item["artist_match"]]
    if artist_only:
        return artist_only[0]
    submitter_only = [item for item in matches if item["submitter_match"]]
    if submitter_only:
        return submitter_only[0]
    return None


def _resolve_parent_folder(service: Any, payload: Any) -> Tuple[str, Dict[str, Any]]:
    root_folder_id = (settings.GOOGLE_DRIVE_ROOT_FOLDER_ID or "").strip()
    matching = _resolve_matching_client_record(payload)

    if matching:
        record = matching["record"]
        fields = record.get("fields", {})

        artist_folder_id = _extract_folder_id(
            str(_get_field_value(fields, settings.AIRTABLE_CLIENT_ARTIST_FOLDER_ID_FIELD) or "")
        )
        projects_folder_id = _extract_folder_id(
            str(_get_field_value(fields, settings.AIRTABLE_CLIENT_PROJECTS_FOLDER_ID_FIELD) or "")
        )
        drive_link_folder_id = _extract_folder_id(
            str(_get_field_value(fields, settings.AIRTABLE_CLIENT_DRIVE_LINK_FIELD) or "")
        )

        effective_artist_folder_id = artist_folder_id or drive_link_folder_id
        logger.info(
            "Google Drive artist matched: client_name=%s label_name=%s record_id=%s",
            matching["client_name"],
            matching["label_name"],
            record["id"],
        )
        logger.info(
            "Google Drive folder candidates: artist_folder_id=%s projects_folder_id=%s drive_link_folder_id=%s",
            artist_folder_id,
            projects_folder_id,
            drive_link_folder_id,
        )

        if projects_folder_id:
            cached_projects_folder = _get_folder_by_id(service, projects_folder_id)
            if cached_projects_folder:
                logger.info(
                    "Google Drive projects folder cache hit: client_name=%s projects_folder_id=%s",
                    matching["client_name"],
                    projects_folder_id,
                )
                return cached_projects_folder["id"], {
                    "strategy": "airtable_existing_projects_folder",
                    "record_id": record["id"],
                    "client_name": matching["client_name"],
                    "label_name": matching["label_name"],
                    "artist_folder_id": effective_artist_folder_id,
                    "projects_folder_id": cached_projects_folder["id"],
                    "projects_folder_created": False,
                    "updated_airtable": False,
                }

            logger.warning(
                "Google Drive projects folder cache miss; falling back to artist folder: client_name=%s cached_projects_folder_id=%s artist_folder_id=%s",
                matching["client_name"],
                projects_folder_id,
                effective_artist_folder_id,
            )

        artist_folder = _get_folder_by_id(service, effective_artist_folder_id)
        if artist_folder:
            logger.info(
                "Google Drive artist folder source: client_name=%s artist_folder_id=%s",
                matching["client_name"],
                artist_folder["id"],
            )
            projetos_folder = _ensure_folder(
                service,
                parent_id=artist_folder["id"],
                name=_PROJECTS_FOLDER_NAME,
            )
            should_update_airtable = projetos_folder["id"] != projects_folder_id
            if should_update_airtable:
                _airtable_update_record(
                    record["id"],
                    {settings.AIRTABLE_CLIENT_PROJECTS_FOLDER_ID_FIELD: projetos_folder["id"]},
                )
                logger.info(
                    "Google Drive projects folder cached in Airtable: client_name=%s projects_folder_id=%s",
                    matching["client_name"],
                    projetos_folder["id"],
                )

            logger.info(
                "Google Drive projects folder ready: client_name=%s artist_folder_id=%s projects_folder_id=%s created=%s",
                matching["client_name"],
                artist_folder["id"],
                projetos_folder["id"],
                projetos_folder.get("created", False),
            )
            return projetos_folder["id"], {
                "strategy": (
                    "airtable_projects_folder_cache_refreshed"
                    if projects_folder_id
                    else "airtable_auto_created_projects_folder"
                ),
                "record_id": record["id"],
                "client_name": matching["client_name"],
                "label_name": matching["label_name"],
                "artist_folder_id": artist_folder["id"],
                "projects_folder_id": projetos_folder["id"],
                "projects_folder_created": projetos_folder.get("created", False),
                "updated_airtable": should_update_airtable,
            }

        raise RuntimeError(
            "Matched Airtable client without accessible artist/projects folder: "
            f"{matching['client_name']}"
        )

    if root_folder_id:
        logger.info("Google Drive using root fallback folder: root_folder_id=%s", root_folder_id)
        return root_folder_id, {"strategy": "root_fallback"}

    raise RuntimeError("Could not resolve Google Drive parent folder.")


def sync_submission_to_google_drive(payload: Any) -> Dict[str, Any]:
    if not settings.GOOGLE_DRIVE_ENABLED:
        return {"ok": True, "status": "skipped", "reason": "disabled"}

    try:
        service = _build_drive_service()
        parent_folder_id, routing = _resolve_parent_folder(service, payload)
        release_folder = _ensure_folder(
            service,
            parent_id=parent_folder_id,
            name=_build_release_folder_name(payload),
        )
        logger.info(
            "Google Drive release folder ready: folder_id=%s parent_folder_id=%s name=%s",
            release_folder["id"],
            parent_folder_id,
            release_folder["name"],
        )

        subfolders = {
            "Audios": _ensure_folder(service, parent_id=release_folder["id"], name="Audios"),
            "Capa": _ensure_folder(service, parent_id=release_folder["id"], name="Capa"),
            "Imprensa": _ensure_folder(service, parent_id=release_folder["id"], name="Imprensa"),
            "Imagens e Videos": _ensure_folder(service, parent_id=release_folder["id"], name="Imagens e Videos"),
            "Outros": _ensure_folder(service, parent_id=release_folder["id"], name="Outros"),
        }

        uploaded_files: List[Dict[str, Any]] = []
        upload_errors: List[str] = []
        project = getattr(payload, "project", None)
        marketing = getattr(payload, "marketing", None)

        cover_file = getattr(project, "cover_file", None)
        if cover_file:
            try:
                created = _upload_file_from_ref(
                    service,
                    folder_id=subfolders["Capa"]["id"],
                    file_ref=cover_file,
                    fallback_name="cover",
                )
                if created:
                    uploaded_files.append({"bucket": "Capa", "file": created})
            except Exception as exc:
                logger.exception("Google Drive cover upload failed")
                upload_errors.append(f"Capa: {exc}")

        for index, track in enumerate(getattr(payload, "tracks", []) or [], start=1):
            audio_file = getattr(track, "audio_file", None)
            if not audio_file:
                continue
            try:
                created = _upload_file_from_ref(
                    service,
                    folder_id=subfolders["Audios"]["id"],
                    file_ref=audio_file,
                    fallback_name=f"track-{index}.wav",
                )
                if created:
                    uploaded_files.append({"bucket": "Audios", "file": created})
            except Exception as exc:
                logger.exception(
                    "Google Drive audio upload failed track_index=%s track_title=%s",
                    index,
                    getattr(track, "title", None),
                )
                upload_errors.append(f"Audios track {index}: {exc}")

        for index, extra_file in enumerate(getattr(marketing, "additional_files", []) or [], start=1):
            try:
                created = _upload_file_from_ref(
                    service,
                    folder_id=subfolders["Outros"]["id"],
                    file_ref=extra_file,
                    fallback_name=f"extra-{index}",
                )
                if created:
                    uploaded_files.append({"bucket": "Outros", "file": created})
            except Exception as exc:
                logger.exception("Google Drive additional file upload failed file_index=%s", index)
                upload_errors.append(f"Outros file {index}: {exc}")

        return {
            "ok": True,
            "status": "ok" if not upload_errors else "partial",
            "routing": routing,
            "parent_folder_id": parent_folder_id,
            "release_folder": release_folder,
            "subfolders": subfolders,
            "uploaded_files": uploaded_files,
            "upload_errors": upload_errors,
        }
    except Exception as exc:
        logger.exception("Google Drive sync failed at top level")
        return {"ok": False, "status": "failed", "error": str(exc)}
