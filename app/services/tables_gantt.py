from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services.airtable import _projects_table_name, _request_json, _table_url
from app.services.workspace_config import get_airtable_extra_config


DEFAULT_MACROAREAS: List[Dict[str, Any]] = [
    {"name": "Clearance", "offset_start": -60, "offset_end": -21, "color": "#B91C1C"},
    {"name": "Operacional", "offset_start": -45, "offset_end": -14, "color": "#2563EB"},
    {"name": "Plano de Marketing", "offset_start": -55, "offset_end": -40, "color": "#15803D"},
    {"name": "Plano de Midia", "offset_start": -40, "offset_end": -10, "color": "#CA8A04"},
    {"name": "Videoclipe", "offset_start": -21, "offset_end": -7, "color": "#DB2777"},
    {"name": "Imprensa", "offset_start": -21, "offset_end": -15, "color": "#7C3AED"},
    {"name": "Relatorio D+7", "offset_start": 7, "offset_end": 10, "color": "#EA580C"},
    {"name": "Relatorio D+15", "offset_start": 15, "offset_end": 18, "color": "#EA580C"},
    {"name": "Relatorio D+28", "offset_start": 28, "offset_end": 33, "color": "#EA580C"},
]

PROJECT_NAME_FIELDS = ["Nome do Projeto", "Projeto", "Título do Projeto", "Name", "Nome"]
PROJECT_RELEASE_DATE_FIELDS = ["Data de Lançamento", "Release Date", "Data Lancamento"]
PROJECT_VIDEO_FIELDS = [
    "Tem Videoclipe / Lyric / Visualizer",
    "Tem Videoclipe",
    "Videoclipe",
]

STAGE_TITLE_FIELDS = ["Nome da Etapa", "Etapa", "Tarefa", "Name", "Nome"]
STAGE_PROJECT_FIELDS = ["Projeto Musical", "Projeto", "Nome do Projeto"]
STAGE_MACROAREA_FIELDS = ["Macroárea", "Macroarea", "Macro Área", "Área", "Area"]
STAGE_START_FIELDS = ["Data Início", "Data de Início", "Início", "Inicio", "Data Inicio"]
STAGE_END_FIELDS = ["Data Fim", "Data de Fim", "Fim"]
STAGE_RELEASE_DATE_FIELDS = ["Data de Lançamento", "Release Date"]
STAGE_STATUS_FIELDS = ["Status", "Status da Etapa", "Situação", "Situacao"]
STAGE_RESPONSIBLE_FIELDS = ["Responsável", "Responsavel", "Owner"]
STAGE_ACTIVE_FIELDS = ["Ativa", "Ativo", "Active"]
STAGE_OFFSET_START_FIELDS = [
    "Offset Inicio (dias)",
    "Offset Início (dias)",
    "Offset Início",
    "Offset Inicio",
    "Offset Start",
]
STAGE_OFFSET_END_FIELDS = ["Offset Fim (dias)", "Offset Fim", "Offset End"]

DONE_STATUS_VALUES = {
    "concluida",
    "concluída",
    "feito",
    "finalizada",
    "done",
    "completed",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    return date.today().isoformat()


def _first_value(fields: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        value = fields.get(key)
        if value not in (None, "", []):
            return value
    return None


def _text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item not in (None, ""))
    return str(value or "").strip()


def _list_values(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _parse_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _is_truthy(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text not in {"nao", "não", "no", "false", "0", "inativo", "desativado"}


def _has_video(fields: Dict[str, Any]) -> bool:
    value = _first_value(fields, PROJECT_VIDEO_FIELDS)
    return _is_truthy(value, default=False)


def _macroarea_config(workspace_slug: str) -> List[Dict[str, Any]]:
    extra = get_airtable_extra_config(workspace_slug, "release_intake")
    raw = extra.get("gantt_macroareas") or extra.get("macroareas")
    if not isinstance(raw, list):
        return DEFAULT_MACROAREAS

    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name") or item.get("nome"))
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "offset_start": _parse_int(item.get("offset_start") or item.get("offset_inicio")) or 0,
                "offset_end": _parse_int(item.get("offset_end") or item.get("offset_fim")) or 0,
                "color": _text(item.get("color") or item.get("cor")) or "#111111",
            }
        )
    return normalized or DEFAULT_MACROAREAS


def _base_id(workspace_slug: str) -> Optional[str]:
    extra = get_airtable_extra_config(workspace_slug, "release_intake")
    return extra.get("base_id_override") or None


def _stages_table_name(workspace_slug: str) -> str:
    extra = get_airtable_extra_config(workspace_slug, "release_intake")
    return (
        _text(extra.get("gantt_stages_table_override"))
        or _text(getattr(settings, "AIRTABLE_GANTT_STAGES_TABLE", ""))
        or "[V2] Etapas do Lançamento"
    )


def _projects_table_for_gantt(workspace_slug: str) -> str:
    extra = get_airtable_extra_config(workspace_slug, "release_intake")
    return (
        _text(extra.get("gantt_projects_table_override"))
        or _text(getattr(settings, "AIRTABLE_GANTT_PROJECTS_TABLE", ""))
        or _text(extra.get("projects_table_override"))
        or _projects_table_name()
    )


def _list_airtable_records(
    *,
    base_id: Optional[str],
    table_name: str,
    max_records: int,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    offset: Optional[str] = None
    while len(records) < max_records:
        params: Dict[str, Any] = {"pageSize": 100}
        if offset:
            params["offset"] = offset
        data = _request_json("GET", _table_url(table_name, base_id), params=params)
        batch = data.get("records", [])
        if isinstance(batch, list):
            records.extend(batch[: max_records - len(records)])
        offset = data.get("offset")
        if not offset:
            break
    return records


def _project_lookup(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for record in records:
        record_id = str(record.get("id") or "").strip()
        if not record_id:
            continue
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        project_name = _text(_first_value(fields, PROJECT_NAME_FIELDS)) or record_id
        lookup[record_id] = {
            "name": project_name,
            "release_date": _parse_date(_first_value(fields, PROJECT_RELEASE_DATE_FIELDS)),
        }
    return lookup


def _stage_project_ref(
    fields: Dict[str, Any],
    project_lookup: Dict[str, Dict[str, Any]],
) -> Dict[str, str]:
    raw_value = _first_value(fields, STAGE_PROJECT_FIELDS)
    values = _list_values(raw_value)
    project_id = values[0] if values else ""
    if not project_id:
        return {
            "id": "",
            "name": "Sem projeto vinculado",
        }
    if project_id in project_lookup:
        return {
            "id": project_id,
            "name": str(project_lookup[project_id].get("name") or project_id),
        }
    return {
        "id": project_id,
        "name": _text(raw_value),
    }


def _status_for_item(status: str, end_date: Optional[date], today: date) -> Dict[str, Any]:
    normalized = status.strip().lower()
    is_done = normalized in DONE_STATUS_VALUES
    is_overdue = bool(end_date and end_date < today and not is_done)
    if is_done:
        value = "concluida"
        label = "Concluída"
    elif is_overdue:
        value = "atrasada"
        label = "Atrasada"
    elif normalized:
        value = normalized.replace(" ", "_")
        label = status
    else:
        value = "planejada"
        label = "Planejada"
    return {"value": value, "label": label, "is_overdue": is_overdue}


def _item_payload(
    *,
    record_id: str,
    project_id: str,
    project_name: str,
    title: str,
    macroarea: str,
    color: str,
    start_date: Optional[date],
    end_date: Optional[date],
    release_date: Optional[date],
    status: str,
    responsible: str,
    active: bool,
    source: str,
) -> Dict[str, Any]:
    today = date.today()
    status_payload = _status_for_item(status, end_date, today)
    return {
        "id": record_id,
        "project_id": project_id,
        "project_name": project_name or "Projeto sem nome",
        "title": title or macroarea,
        "macroarea": macroarea,
        "color": color,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "release_date": release_date.isoformat() if release_date else None,
        "status": status_payload["value"],
        "status_label": status_payload["label"],
        "responsible": responsible,
        "active": active,
        "is_overdue": status_payload["is_overdue"],
        "source": source,
    }


def _records_from_stages(
    *,
    workspace_slug: str,
    records: List[Dict[str, Any]],
    project_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    macroareas = {item["name"]: item for item in _macroarea_config(workspace_slug)}
    project_lookup = project_lookup or {}
    items: List[Dict[str, Any]] = []
    for record in records:
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        project_ref = _stage_project_ref(fields, project_lookup)
        macroarea = _text(_first_value(fields, STAGE_MACROAREA_FIELDS)) or "Operacional"
        cfg = macroareas.get(macroarea, {})
        release_date = _parse_date(_first_value(fields, STAGE_RELEASE_DATE_FIELDS))
        if not release_date and project_ref["id"] in project_lookup:
            release_date = project_lookup[project_ref["id"]].get("release_date")
        start_date = _parse_date(_first_value(fields, STAGE_START_FIELDS))
        end_date = _parse_date(_first_value(fields, STAGE_END_FIELDS))

        if release_date and not start_date:
            offset = _parse_int(_first_value(fields, STAGE_OFFSET_START_FIELDS))
            if offset is not None:
                start_date = release_date + timedelta(days=offset)
        if release_date and not end_date:
            offset = _parse_int(_first_value(fields, STAGE_OFFSET_END_FIELDS))
            if offset is not None:
                end_date = release_date + timedelta(days=offset)

        items.append(
            _item_payload(
                record_id=str(record.get("id") or ""),
                project_id=project_ref["id"],
                project_name=project_ref["name"],
                title=_text(_first_value(fields, STAGE_TITLE_FIELDS)) or macroarea,
                macroarea=macroarea,
                color=_text(cfg.get("color")) or "#111111",
                start_date=start_date,
                end_date=end_date,
                release_date=release_date,
                status=_text(_first_value(fields, STAGE_STATUS_FIELDS)),
                responsible=_text(_first_value(fields, STAGE_RESPONSIBLE_FIELDS)),
                active=_is_truthy(_first_value(fields, STAGE_ACTIVE_FIELDS), default=True),
                source="stages",
            )
        )
    return items


def _records_from_projects(
    *,
    workspace_slug: str,
    records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    macroareas = _macroarea_config(workspace_slug)
    items: List[Dict[str, Any]] = []
    for record in records:
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        project_id = str(record.get("id") or "")
        project_name = _text(_first_value(fields, PROJECT_NAME_FIELDS)) or project_id
        release_date = _parse_date(_first_value(fields, PROJECT_RELEASE_DATE_FIELDS))
        has_video = _has_video(fields)
        for cfg in macroareas:
            macroarea = str(cfg["name"])
            if macroarea == "Videoclipe" and not has_video:
                continue
            start_date = release_date + timedelta(days=int(cfg["offset_start"])) if release_date else None
            end_date = release_date + timedelta(days=int(cfg["offset_end"])) if release_date else None
            items.append(
                _item_payload(
                    record_id=f"{project_id}:{macroarea}",
                    project_id=project_id,
                    project_name=project_name,
                    title=macroarea,
                    macroarea=macroarea,
                    color=str(cfg["color"]),
                    start_date=start_date,
                    end_date=end_date,
                    release_date=release_date,
                    status="Planejada",
                    responsible="",
                    active=True,
                    source="projects_fallback",
                )
            )
    return items


def _summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    projects = {item["project_name"] for item in items if item.get("project_name")}
    return {
        "total": len(items),
        "active": len([item for item in items if item.get("active")]),
        "overdue": len([item for item in items if item.get("is_overdue")]),
        "without_date": len([item for item in items if not item.get("start_date") or not item.get("end_date")]),
        "projects": len(projects),
    }


def _filters(items: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    def unique(key: str) -> List[str]:
        return sorted({str(item.get(key) or "").strip() for item in items if str(item.get(key) or "").strip()})

    return {
        "macroareas": unique("macroarea"),
        "statuses": unique("status_label"),
        "responsibles": unique("responsible"),
        "projects": unique("project_name"),
    }


def build_gantt_response(
    *,
    workspace_slug: str,
    max_records: int = 200,
) -> Dict[str, Any]:
    base_id = _base_id(workspace_slug)
    warnings: List[str] = []
    source = "stages"
    items: List[Dict[str, Any]] = []

    try:
        stage_records = _list_airtable_records(
            base_id=base_id,
            table_name=_stages_table_name(workspace_slug),
            max_records=max_records,
        )
        try:
            project_records = _list_airtable_records(
                base_id=base_id,
                table_name=_projects_table_for_gantt(workspace_slug),
                max_records=max_records,
            )
            lookup = _project_lookup(project_records)
        except Exception as exc:
            warnings.append(f"Não foi possível resolver nomes de projetos; usando referência da etapa. Detalhe: {exc}")
            lookup = {}
        items = _records_from_stages(workspace_slug=workspace_slug, records=stage_records, project_lookup=lookup)
    except Exception as exc:
        warnings.append(f"Tabela de etapas indisponível; usando fallback de projetos. Detalhe: {exc}")
        source = "projects_fallback"
        project_records = _list_airtable_records(
            base_id=base_id,
            table_name=_projects_table_for_gantt(workspace_slug),
            max_records=max_records,
        )
        items = _records_from_projects(workspace_slug=workspace_slug, records=project_records)

    items.sort(key=lambda item: (item.get("start_date") or "9999-12-31", item.get("project_name") or ""))
    return {
        "ok": True,
        "workspace_slug": workspace_slug,
        "source": source,
        "generated_at": _utc_now_iso(),
        "today": _today_iso(),
        "items": items,
        "summary": _summary(items),
        "filters": _filters(items),
        "warnings": warnings,
    }
