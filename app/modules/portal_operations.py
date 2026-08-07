"""Leitura operacional real para a área do cliente.

Nenhum dado demonstrativo é produzido aqui. A fonte primária é o Airtable da
operação; quando ele estiver indisponível, usamos apenas submissões reais já
persistidas no Supabase. O endpoint é protegido pela sessão do portal.
"""
from __future__ import annotations

from datetime import datetime
import logging
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header

from app.core.admin_auth import _admin_token_is_valid
from app.core.config import settings
from app.core.database import supabase
from app.modules.portal_session import require_portal_session
from app.services.airtable import _base_id, _request_json, _table_url
from app.services.people_registry_invites import list_people_registry_invites_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspaces", tags=["portal-operations"])

PROJECTS_TABLE = "[V2] Projetos Musicais"
TRACKS_TABLE = "[V2] Faixas Musicais"
STAGES_TABLE = "[V2] Etapas do Lançamento"
DEMANDS_TABLE = "[V2] Demandas Operacionais"
CALENDAR_TABLE = "[V2] Calendário de Lançamentos"


async def _require_portal_or_admin(
    workspace_slug: str,
    x_portal_token: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> None:
    if x_admin_token and _admin_token_is_valid(x_admin_token.strip()):
        return
    require_portal_session(workspace_slug, x_portal_token)


def _records(table: str, fields: Iterable[str], limit: int = 100) -> List[Dict[str, Any]]:
    data = _request_json(
        "GET",
        _table_url(table),
        params={"pageSize": min(limit, 100), "maxRecords": limit, "fields[]": list(fields)},
    )
    rows = data.get("records")
    return rows if isinstance(rows, list) else []


def _records_by_ids(table: str, record_ids: Iterable[Any], fields: Iterable[str]) -> List[Dict[str, Any]]:
    ids = list(dict.fromkeys(
        str(record_id) for record_id in record_ids
        if re.fullmatch(r"rec[A-Za-z0-9]+", str(record_id or ""))
    ))[:100]
    if not ids:
        return []
    formula = "OR(" + ",".join(f"RECORD_ID()='{record_id}'" for record_id in ids) + ")"
    data = _request_json(
        "GET",
        _table_url(table),
        params={"pageSize": 100, "filterByFormula": formula, "fields[]": list(fields)},
    )
    rows = data.get("records")
    return rows if isinstance(rows, list) else []


def _text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item not in (None, ""))
    if isinstance(value, dict):
        return str(value.get("name") or value.get("filename") or "")
    return str(value or "").strip()


def _has_value(value: Any) -> bool:
    return bool(value and (not isinstance(value, list) or len(value) > 0))


def _urls(value: Any) -> List[str]:
    values = value if isinstance(value, list) else [value]
    urls: List[str] = []
    for item in values:
        candidate = item.get("url") if isinstance(item, dict) else item
        if isinstance(candidate, str) and candidate.strip().lower().startswith(("https://", "http://")):
            urls.append(candidate.strip())
    return list(dict.fromkeys(urls))


def _iso(value: Any) -> Optional[str]:
    raw = _text(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return raw[:10] if len(raw) >= 10 else raw


def _looks_like_test(value: Any) -> bool:
    normalized = re.sub(r"\s+", " ", _text(value).lower()).strip()
    return normalized in {"teste", "test", "qa"} or normalized.startswith(("[teste", "teste ", "test ", "[test", "qa "))


def _submission_rows(workspace_slug: str, limit: int = 100) -> List[Dict[str, Any]]:
    try:
        result = (
            supabase.table("submissions")
            .select(
                "id,release_title,main_title,artist_name,release_type,release_date,status,"
                "airtable_sync_status,airtable_sync_error,airtable_project_id,airtable_synced_at,"
                "google_drive_folder_id,email_status,email_sent_at,created_at,submitted_at,payload"
            )
            .eq("client_slug", workspace_slug)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data if isinstance(result.data, list) else []
    except Exception as exc:
        logger.warning("portal operations: submissions unavailable: %s", exc)
        return []


def _fallback_projects(submissions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    projects: List[Dict[str, Any]] = []
    for row in submissions:
        title = row.get("release_title") or row.get("main_title")
        if not title or _looks_like_test(title):
            continue
        projects.append({
            "id": row.get("airtable_project_id") or row.get("id"),
            "title": title,
            "artist": row.get("artist_name") or "",
            "release_type": row.get("release_type") or "",
            "release_date": _iso(row.get("release_date")),
            "status": row.get("status") or "Enviado",
            "track_count": 0,
            "cover_status": "unknown",
            "audio_status": "unknown",
            "isrc_status": "unknown",
            "sync_status": row.get("airtable_sync_status") or "pending",
            "created_at": row.get("created_at"),
        })
    return projects


def _airtable_data() -> Dict[str, Any]:
    project_rows = _records(PROJECTS_TABLE, [
        "Nome do Projeto", "Artistas Principais (from [V2] Faixas Musicais)",
        "Tipo de Lançamento", "Data de Lançamento", "Capa do Projeto", "Faixas",
        "Status Geral do Projeto", "Gênero Musical", "Criado em", "Origem Projeto ID",
        "Automação V2 — Sincronizado em", "Link da Capa",
    ], 100)
    track_rows = _records(TRACKS_TABLE, [
        "Título da Faixa", "Projeto", "Código ISRC", "Link do Áudio (WAV)",
        "Artistas Principais", "Criado em:",
    ], 200)
    stage_rows = _records(STAGES_TABLE, [
        "Etapa", "Macroarea", "Status", "Ativa", "Responsavel", "Projeto Musical",
        "Data de Lançamento", "Data Inicio", "Data Fim", "Risco", "Label responsável",
    ], 200)
    demand_rows = _records(DEMANDS_TABLE, [
        "Ticket da Demanda", "Produto", "Data de Lançamento", "Status da Demanda",
        "Tipo de Demanda", "Data Limite Calculada", "Dias Restantes", "Status de Upload",
        "Origem Projeto ID", "Produto (from Produto)",
    ], 100)
    demand_product_ids = [
        record_id
        for row in demand_rows
        for record_id in ((row.get("fields") or {}).get("Produto") or [])
    ]
    calendar_rows = _records_by_ids(
        CALENDAR_TABLE, demand_product_ids, ["Produto", "Origem Projeto ID"]
    )
    calendar_by_id = {
        str(row.get("id") or ""): row.get("fields") or {}
        for row in calendar_rows
    }

    tracks_by_project: Dict[str, List[Dict[str, Any]]] = {}
    for row in track_rows:
        for project_id in row.get("fields", {}).get("Projeto") or []:
            tracks_by_project.setdefault(str(project_id), []).append(row)

    names: Dict[str, str] = {}
    files_by_project: Dict[str, List[Dict[str, str]]] = {}
    projects: List[Dict[str, Any]] = []
    for row in project_rows:
        fields = row.get("fields") or {}
        title = _text(fields.get("Nome do Projeto"))
        if not title or _looks_like_test(title):
            continue
        project_id = str(row.get("id") or "")
        names[project_id] = title
        tracks = tracks_by_project.get(project_id, [])
        track_fields = [track.get("fields") or {} for track in tracks]
        isrc_values = [_text(item.get("Código ISRC")) for item in track_fields]
        audio_values = [item.get("Link do Áudio (WAV)") for item in track_fields]
        project_files: List[Dict[str, str]] = []
        for url in _urls(fields.get("Link da Capa")):
            project_files.append({"label": "Capa", "url": url})
        for index, item in enumerate(track_fields, start=1):
            for url in _urls(item.get("Link do Áudio (WAV)")):
                project_files.append({"label": f"Áudio {index}", "url": url})
        files_by_project[project_id] = project_files
        projects.append({
            "id": project_id,
            "title": title,
            "artist": _text(fields.get("Artistas Principais (from [V2] Faixas Musicais)")),
            "release_type": _text(fields.get("Tipo de Lançamento")),
            "release_date": _iso(fields.get("Data de Lançamento")),
            "status": _text(fields.get("Status Geral do Projeto")) or "Sem status",
            "genre": _text(fields.get("Gênero Musical")),
            "track_count": len(tracks) or len(fields.get("Faixas") or []),
            "cover_status": "ok" if _has_value(fields.get("Capa do Projeto")) else "pending",
            "audio_status": "ok" if audio_values and all(_has_value(v) for v in audio_values) else "pending",
            "isrc_status": "generated" if isrc_values and all(isrc_values) else "pending",
            "sync_status": "synced" if fields.get("Automação V2 — Sincronizado em") else "source_airtable",
            "created_at": fields.get("Criado em"),
        })

    projects.sort(key=lambda item: str(item.get("release_date") or item.get("created_at") or ""), reverse=True)

    stages: List[Dict[str, Any]] = []
    for row in stage_rows:
        fields = row.get("fields") or {}
        links = fields.get("Projeto Musical") or []
        project_id = str(links[0]) if links else ""
        project_name = names.get(project_id, "")
        if not project_name or _looks_like_test(project_name):
            continue
        stages.append({
            "id": row.get("id"), "project_id": project_id, "project": project_name,
            "name": _text(fields.get("Etapa")), "macroarea": _text(fields.get("Macroarea")),
            "status": _text(fields.get("Status")) or "Sem status",
            "active": fields.get("Ativa") is not False,
            "responsible": _text(fields.get("Label responsável") or fields.get("Responsavel")),
            "start_date": _iso(fields.get("Data Inicio")), "end_date": _iso(fields.get("Data Fim")),
            "completion_date": _iso(
                fields.get("Data de Conclusão")
                or fields.get("Data de Conclusao")
                or fields.get("Concluído em")
                or fields.get("Concluido em")
            ),
            "release_date": _iso(fields.get("Data de Lançamento")), "risk": _text(fields.get("Risco")),
        })

    demands: List[Dict[str, Any]] = []
    for row in demand_rows:
        fields = row.get("fields") or {}
        linked_products = fields.get("Produto") or []
        calendar_fields = next(
            (calendar_by_id.get(str(record_id)) for record_id in linked_products if calendar_by_id.get(str(record_id))),
            {},
        )
        origin_project_id = _text(fields.get("Origem Projeto ID")) or _text(calendar_fields.get("Origem Projeto ID"))
        project_id = origin_project_id if origin_project_id in names else next(
            (str(record_id) for record_id in linked_products if str(record_id) in names), ""
        )
        product = _text(fields.get("Produto (from Produto)")) or names.get(project_id, "")
        if _looks_like_test(product):
            continue
        demands.append({
            "id": row.get("id"), "ticket": _text(fields.get("Ticket da Demanda")),
            "product": product, "type": _text(fields.get("Tipo de Demanda")),
            "status": _text(fields.get("Status da Demanda")) or "Sem status",
            "release_date": _iso(fields.get("Data de Lançamento")),
            "deadline": _iso(fields.get("Data Limite Calculada")),
            "days_remaining": fields.get("Dias Restantes"),
            "upload_status": _text(fields.get("Status de Upload")),
            "project_id": project_id,
            "project_title": names.get(project_id, ""),
            "file_links": files_by_project.get(project_id, []),
        })

    return {"projects": projects, "stages": stages, "demands": demands}


def _invite_items(workspace_slug: str) -> List[Dict[str, Any]]:
    response = list_people_registry_invites_response(workspace_slug=workspace_slug, limit=100)
    dumped = response.model_dump(mode="json")
    return dumped.get("items") or []


@router.get("/{workspace_slug}/portal-data")
async def get_portal_data(
    workspace_slug: str,
    _: None = Depends(_require_portal_or_admin),
) -> Dict[str, Any]:
    slug = workspace_slug.strip().lower()
    submissions = _submission_rows(slug)
    airtable_error: Optional[str] = None
    try:
        live = _airtable_data()
        source = "airtable"
    except Exception as exc:
        logger.exception("portal operations: Airtable unavailable")
        airtable_error = "Airtable temporariamente indisponível"
        live = {"projects": _fallback_projects(submissions), "stages": [], "demands": []}
        source = "supabase"

    drive_folders = []
    email_activity = []
    drive_by_project: Dict[str, str] = {}
    for row in submissions:
        title = row.get("release_title") or row.get("main_title") or "Submissão"
        if _looks_like_test(title):
            continue
        folder_id = row.get("google_drive_folder_id")
        if folder_id:
            project_id = _text(row.get("airtable_project_id"))
            if project_id:
                drive_by_project.setdefault(project_id, str(folder_id))
            drive_folders.append({
                "submission_id": row.get("id"), "project": title, "folder_id": folder_id,
                "project_id": project_id,
                "url": f"https://drive.google.com/drive/folders/{quote(str(folder_id), safe='')}",
                "created_at": row.get("created_at"),
            })
        if row.get("email_status") or row.get("email_sent_at"):
            email_activity.append({
                "submission_id": row.get("id"), "project": title,
                "status": row.get("email_status") or "sent", "sent_at": row.get("email_sent_at"),
            })

    for demand in live.get("demands") or []:
        folder_id = drive_by_project.get(_text(demand.get("project_id")))
        if folder_id:
            demand["file_links"] = [
                {"label": "Pasta do projeto", "url": f"https://drive.google.com/drive/folders/{quote(folder_id, safe='')}"},
                *(demand.get("file_links") or []),
            ]

    sync_rows = [row for row in submissions if not _looks_like_test(row.get("release_title") or row.get("main_title"))]
    synced = sum(1 for row in sync_rows if row.get("airtable_sync_status") == "synced")
    failed = sum(1 for row in sync_rows if row.get("airtable_sync_status") == "failed")

    return {
        "ok": True, "workspace_slug": slug, "source": source, "source_error": airtable_error,
        **live,
        "invites": _invite_items(slug),
        "drive_folders": drive_folders[:50],
        "email_activity": email_activity[:50],
        "sync_summary": {"total": len(sync_rows), "synced": synced, "failed": failed},
        "integrations": {
            "airtable": {"configured": bool(settings.AIRTABLE_API_KEY and settings.AIRTABLE_BASE_ID), "status": "online" if source == "airtable" else "degraded"},
            "drive": {"configured": bool(settings.GOOGLE_DRIVE_ENABLED and settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON), "status": "online" if settings.GOOGLE_DRIVE_ENABLED else "disabled"},
            "email": {"configured": bool(settings.RESEND_API_KEY), "status": "online" if settings.RESEND_API_KEY else "disabled"},
            "lyrics_ai": {"configured": bool(getattr(settings, "GEMINI_LYRICS_API_KEY", None)), "status": "online" if getattr(settings, "GEMINI_LYRICS_API_KEY", None) else "disabled"},
        },
    }
