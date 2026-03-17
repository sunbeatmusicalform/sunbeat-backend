from __future__ import annotations

import time
import unicodedata
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

import httpx

from app.core.config import settings

AIRTABLE_API_URL = "https://api.airtable.com/v0"
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 3
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


def _airtable_headers() -> Dict[str, str]:
    if not settings.AIRTABLE_API_KEY:
        raise RuntimeError("Missing required environment variable: AIRTABLE_API_KEY")

    return {
        "Authorization": f"Bearer {settings.AIRTABLE_API_KEY}",
        "Content-Type": "application/json",
    }


def _base_id() -> str:
    if not settings.AIRTABLE_BASE_ID:
        raise RuntimeError("Missing required environment variable: AIRTABLE_BASE_ID")
    return settings.AIRTABLE_BASE_ID


def _projects_table_name() -> str:
    table_name = getattr(settings, "AIRTABLE_PROJECTS_TABLE", None)
    if not table_name:
        raise RuntimeError("Missing required environment variable: AIRTABLE_PROJECTS_TABLE")
    return table_name


def _tracks_table_name() -> str:
    table_name = getattr(settings, "AIRTABLE_TRACKS_TABLE", None)
    if not table_name:
        raise RuntimeError("Missing required environment variable: AIRTABLE_TRACKS_TABLE")
    return table_name


def _track_project_link_field() -> str:
    field_name = getattr(settings, "AIRTABLE_TRACK_PROJECT_LINK_FIELD", None)
    if not field_name:
        raise RuntimeError("Missing required environment variable: AIRTABLE_TRACK_PROJECT_LINK_FIELD")
    return field_name


def _project_focus_track_field() -> str:
    return getattr(settings, "AIRTABLE_PROJECT_FOCUS_TRACK_FIELD", "Faixa Foco")


def _table_url(table_name: str) -> str:
    base_id = _base_id()
    encoded_table_name = quote(table_name, safe="")
    return f"{AIRTABLE_API_URL}/{base_id}/{encoded_table_name}"


def _request_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    headers = _airtable_headers()
    last_error: Optional[str] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                response = client.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    json=payload,
                    params=params,
                )

            if response.status_code in RETRY_STATUS_CODES and attempt < MAX_RETRIES:
                time.sleep(0.8 * attempt)
                continue

            try:
                data: Dict[str, Any] = response.json()
            except Exception:
                data = {"raw": response.text}

            if response.status_code >= 400:
                raise RuntimeError(f"Airtable HTTP {response.status_code}: {data}")

            return data

        except Exception as exc:
            last_error = str(exc)
            if attempt < MAX_RETRIES:
                time.sleep(0.8 * attempt)
                continue

    raise RuntimeError(last_error or "Unknown Airtable error")


def _chunk(items: List[Dict[str, Any]], size: int = 10) -> Iterable[List[Dict[str, Any]]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _clean_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}

    for key, value in fields.items():
        if value is None:
            continue

        if isinstance(value, str):
            value = value.strip()
            if value == "":
                continue

        if isinstance(value, list):
            value = [item for item in value if item not in (None, "")]
            if not value:
                continue

        cleaned[key] = value

    return cleaned


def _normalize_yes_no(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip().lower()
    if text in {"yes", "sim", "true", "1"}:
        return "Sim"
    if text in {"no", "n\u00e3o", "nao", "false", "0"}:
        return "Nao"

    return str(value).strip()


def _normalize_release_type(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip().lower()
    if text == "single":
        return "Single"
    if text == "ep":
        return "EP"
    if text == "album":
        return "Album"
    return str(value).strip()


def _normalize_artist_profile_status(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip().lower()
    if text == "already_exists":
        return "Ja tem perfil"
    if text == "needs_creation":
        return "O perfil precisa ser criado"
    if text == "mixed":
        return "Alguns artistas ja possuem perfil, enquanto outros precisam ser criados."

    normalized = unicodedata.normalize("NFKD", str(value).strip())
    return normalized.encode("ascii", "ignore").decode("ascii")


def _coerce_yes_no(primary_value: Any, fallback_value: Any = None) -> Optional[str]:
    normalized = _normalize_yes_no(primary_value)
    if normalized:
        return normalized

    if fallback_value in (None, "", []):
        return None

    return "Sim"


def _attachment_value(file_ref: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(file_ref, dict):
        return None

    public_url = file_ref.get("public_url")
    if not public_url:
        return None

    attachment: Dict[str, Any] = {"url": public_url}
    if file_ref.get("file_name"):
        attachment["filename"] = file_ref["file_name"]
    return attachment


def _attachments_value(file_refs: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(file_refs, dict):
        file_refs = [file_refs]

    if not isinstance(file_refs, list):
        return None

    attachments = []
    for item in file_refs:
        attachment = _attachment_value(item)
        if attachment:
            attachments.append(attachment)

    return attachments or None


def _public_url(file_ref: Any) -> Optional[str]:
    if not isinstance(file_ref, dict):
        return None
    return file_ref.get("public_url") or None


def _linked_record_contains(
    field_value: Any,
    expected_record_id: str,
) -> bool:
    if not isinstance(field_value, list):
        return False

    for item in field_value:
        if item == expected_record_id:
            return True
        if isinstance(item, dict) and item.get("id") == expected_record_id:
            return True

    return False


def _ensure_track_project_link(
    *,
    track_record_id: str,
    project_record_id: str,
    project_link_field: str,
) -> Dict[str, Any]:
    payload = {
        "fields": {
            project_link_field: [project_record_id],
        },
        "typecast": True,
    }

    url = f"{_table_url(_tracks_table_name())}/{track_record_id}"
    return _request_json("PATCH", url, payload)


def create_airtable_project(
    *,
    workspace_slug: str,
    identification: Dict[str, Any],
    project: Dict[str, Any],
    marketing: Dict[str, Any],
    submission_id: str,
    draft_token: Optional[str] = None,
    edit_url: Optional[str] = None,
) -> Dict[str, Any]:
    del workspace_slug, submission_id, draft_token, edit_url

    table_name = _projects_table_name()
    cover_url = project.get("cover_link") or _public_url(project.get("cover_file"))

    fields = _clean_fields(
        {
            "Nome do Projeto": identification.get("project_title"),
            "Tipo de Lan\u00e7amento": _normalize_release_type(
                identification.get("release_type")
            ),
            "Data de Lan\u00e7amento": project.get("release_date"),
            "Nome do Respons\u00e1vel": identification.get("submitter_name"),
            "Email do Respons\u00e1vel": identification.get("submitter_email"),
            "Capa do Projeto": _attachments_value(project.get("cover_file")),
            "Link da Capa": cover_url,
            "G\u00eanero Musical": project.get("genre"),
            "Minutagem do TikTok": project.get("tiktok_snippet"),
            "Conte\u00fado Expl\u00edcito (+18)": _normalize_yes_no(
                project.get("explicit_content")
            ),
            "Tem Videoclipe / Lyric / Visualizer": _normalize_yes_no(
                project.get("has_video_asset")
            ),
            "Link do Videoclipe": project.get("video_link"),
            "Link Fotos / Arquivos de Divulga\u00e7\u00e3o": project.get(
                "promo_assets_link"
            ),
            "Marketing \u2013 N\u00fameros e Resultados": marketing.get(
                "marketing_numbers"
            ),
            "Marketing \u2013 Foco do Lan\u00e7amento": marketing.get(
                "marketing_focus"
            ),
            "Marketing \u2013 Objetivos": marketing.get("marketing_objectives"),
            "Tem Verba para Promo\u00e7\u00e3o": _coerce_yes_no(
                marketing.get("has_marketing_budget"),
                marketing.get("marketing_budget"),
            ),
            "Valor da Verba de Promo\u00e7\u00e3o": marketing.get("marketing_budget"),
            "Flexibilidade na Data de Lan\u00e7amento": marketing.get(
                "date_flexibility"
            ),
            "Link do Presskit": project.get("presskit_link"),
            "Tem Participa\u00e7\u00f5es Especiais": _normalize_yes_no(
                marketing.get("has_special_guests")
            ),
            "Mini Biografia das Participa\u00e7\u00f5es": marketing.get(
                "special_guests_bio"
            ),
            "Feat participar\u00e1 da divulga\u00e7\u00e3o": _normalize_yes_no(
                marketing.get("feat_will_promote")
            ),
            "Participantes na Promo\u00e7\u00e3o": marketing.get(
                "promotion_participants"
            ),
            "Influenciadores / Marcas / Parceiros": marketing.get(
                "influencers_brands_partners"
            ),
            "Observa\u00e7\u00f5es do Projeto": marketing.get("general_notes"),
            "Arquivos Adicionais": _attachments_value(marketing.get("additional_files")),
            "Qual a data de lan\u00e7amento do V\u00eddeo?": project.get(
                "video_release_date"
            ),
        }
    )

    payload = {"records": [{"fields": fields}], "typecast": True}
    data = _request_json("POST", _table_url(table_name), payload)

    records = data.get("records", [])
    if not records:
        raise RuntimeError("Airtable project record was not created")

    return records[0]


def create_airtable_tracks(
    *,
    airtable_project_id: str,
    workspace_slug: str,
    submission_id: str,
    tracks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    del workspace_slug, submission_id

    table_name = _tracks_table_name()
    project_link_field = _track_project_link_field()

    record_payloads: List[Dict[str, Any]] = []

    for track in tracks:
        fields = _clean_fields(
            {
                "T\u00edtulo da Faixa": track.get("title"),
                "Ordem da Faixa": track.get("order_number"),
                project_link_field: [airtable_project_id],
                "Editoras": track.get("publishers"),
                "Produtores / M\u00fasicos": track.get("producers_musicians"),
                "Conte\u00fado Expl\u00edcito (+18)": _normalize_yes_no(
                    track.get("explicit_content")
                ),
                "Possui ISRC?": _normalize_yes_no(track.get("has_isrc")),
                "C\u00f3digo ISRC": track.get("isrc"),
                "Link do \u00c1udio (WAV)": track.get("audio_public_url")
                or track.get("audio_path"),
                "Letra da M\u00fasica": track.get("lyrics"),
                "Artistas Principais": track.get("artists"),
                "Participa\u00e7\u00f5es (Feat)": track.get("feats"),
                "Int\u00e9rpretes": track.get("interpreters"),
                "Autores": track.get("authors"),
                "Produtor Fonogr\u00e1fico": track.get("phonographic_producer"),
                "Escreva exatamente como deve ser o nome do Perfil de cada Artista que precisa ser criado:": track.get(
                    "artist_profile_names_to_create"
                ),
                "Links do Perfil j\u00e1 existente de cada Artista:": track.get(
                    "existing_profile_links"
                ),
                "Os Artistas j\u00e1 tem Perfil nas plataformas ou o Perfil precisa ser criado?": _normalize_artist_profile_status(
                    track.get("artist_profiles_status")
                ),
                "Trecho do Tik Tok - Minutagem": track.get("tiktok_snippet"),
            }
        )
        record_payloads.append({"fields": fields})

    created: List[Dict[str, Any]] = []

    for batch in _chunk(record_payloads, size=10):
        payload = {"records": batch, "typecast": True}
        data = _request_json("POST", _table_url(table_name), payload)
        created.extend(data.get("records", []))

    linked_tracks: List[Dict[str, Any]] = []
    for item in created:
        fields = item.get("fields", {})
        if _linked_record_contains(fields.get(project_link_field), airtable_project_id):
            linked_tracks.append(item)
            continue

        patched = _ensure_track_project_link(
            track_record_id=item["id"],
            project_record_id=airtable_project_id,
            project_link_field=project_link_field,
        )
        linked_tracks.append(patched)

    return linked_tracks


def update_airtable_project_focus_track(
    *,
    airtable_project_id: str,
    airtable_focus_track_id: str,
) -> Dict[str, Any]:
    table_name = _projects_table_name()
    focus_track_field = _project_focus_track_field()

    payload = {
        "fields": {
            focus_track_field: [airtable_focus_track_id],
        }
    }

    url = f"{_table_url(table_name)}/{airtable_project_id}"
    return _request_json("PATCH", url, payload)
