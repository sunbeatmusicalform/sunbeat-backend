from __future__ import annotations

"""
app/services/airtable_rights_clearance.py
──────────────────────────────────────────
Etapa 1 — Syncs Rights Clearance submissions to Airtable [V2] Clearance.

Creates a single record in [V2] Clearance for every supported format.
[V2] Clearance Itens and [V2] Clearance Partes are deferred to Etapa 2+.

Supported formats (all active):
  music_release_clearance_intake  -> creates [V2] Clearance record
  music_project_track             -> creates [V2] Clearance record
  audiovisual_product_sync        -> creates [V2] Clearance record

Returns:
  {
    "skipped": bool,
    "skip_reason": str | None,
    "airtable_project": dict | None,   # the created [V2] Clearance record
    "airtable_tracks": list,           # always [] in Etapa 1
  }
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from app.core.config import settings
from app.services.airtable import _base_id, _request_json

logger = logging.getLogger(__name__)

# All clearance formats currently handled (none deferred)
ACTIVE_FORMATS = {
    "music_release_clearance_intake",
    "music_project_track",
    "audiovisual_product_sync",
}

CLEARANCE_V2_TABLE = "[V2] Clearance"

FORMAT_LABELS: Dict[str, str] = {
    "music_release_clearance_intake": "Clearance – Lançamento Musical",
    "music_project_track": "Clearance – Faixa de Projeto",
    "audiovisual_product_sync": "Clearance – Sincronização Audiovisual",
}

# Maps format -> Escopo singleSelect value in Airtable
FORMAT_ESCOPO: Dict[str, str] = {
    "music_release_clearance_intake": "musical",
    "music_project_track": "musical",
    "audiovisual_product_sync": "nao_musical",
}

# Maps format -> Tipo de Utilizacao singleSelect value in Airtable
FORMAT_TIPO_UTILIZACAO: Dict[str, str] = {
    "music_release_clearance_intake": "Licenciamento",
    "music_project_track": "Licenciamento",
    "audiovisual_product_sync": "Sincronização",
}


# --- Helpers ------------------------------------------------------------------


def _safe_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _safe_dict(obj: Any) -> Dict[str, Any]:
    """Converts a Pydantic model or plain dict to a plain dict safely."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump() or {}
    if hasattr(obj, "dict"):
        return obj.dict() or {}
    return {}


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _build_edit_url(edit_token: str, workspace_slug: str) -> Optional[str]:
    """Constructs the public edit URL for the submission."""
    try:
        from app.modules.workflow_registry import build_frontend_workflow_path

        base = settings.FRONTEND_BASE_URL.rstrip("/")
        path = build_frontend_workflow_path(
            workspace_slug=workspace_slug,
            workflow_type="rights_clearance",
        )
        return f"{base}{path}?edit_token={edit_token}"
    except Exception:
        logger.warning("Could not build edit URL for edit_token=%s", edit_token)
        return None


def _build_observacoes(
    clearance_format: str,
    cs: Dict[str, Any],
    pc: Dict[str, Any],
    ar: Dict[str, Any],
) -> Optional[str]:
    """
    Assembles the Observacoes Operacionais text block from payload fields.
    Returns None if nothing meaningful is present.
    """
    lines: List[str] = []

    if clearance_format in ("music_release_clearance_intake", "music_project_track"):
        if cs.get("music_title"):
            lines.append(f"[Música] {cs['music_title']}")
        if cs.get("artist_name"):
            lines.append(f"[Artista] {cs['artist_name']}")
        if cs.get("phonogram_owner"):
            lines.append(f"[Produtor Fonográfico] {cs['phonogram_owner']}")
        if cs.get("composer_author_info"):
            lines.append(f"[Compositores/Autores] {cs['composer_author_info']}")
        if cs.get("publisher_info"):
            lines.append(f"[Editoras] {cs['publisher_info']}")
        if cs.get("exclusivity"):
            lines.append(f"[Exclusividade] {cs['exclusivity']}")

    if clearance_format == "audiovisual_product_sync":
        if cs.get("audiovisual_type"):
            lines.append(f"[Tipo Audiovisual] {cs['audiovisual_type']}")
        if cs.get("director_name"):
            lines.append(f"[Diretor] {cs['director_name']}")
        if cs.get("scene_description"):
            lines.append(f"[Cena/Contexto] {cs['scene_description']}")
        if cs.get("sync_duration"):
            lines.append(f"[Duração do Uso] {cs['sync_duration']}")
        if cs.get("media_channels"):
            lines.append(f"[Canais/Mídias] {cs['media_channels']}")

    if pc.get("project_synopsis"):
        lines.append(f"[Sinopse] {pc['project_synopsis']}")

    if pc.get("general_clearance_notes"):
        lines.append(f"[Notas Gerais] {pc['general_clearance_notes']}")

    if pc.get("has_brand_association") in ("yes", "sim", True):
        brand = pc.get("brand_context") or ""
        lines.append(f"[Associação de Marca] {'Sim — ' + brand if brand else 'Sim'}")

    if pc.get("responsible_company"):
        lines.append(f"[Empresa Responsável] {pc['responsible_company']}")

    if ar.get("additional_notes"):
        lines.append(f"[Notas Adicionais] {ar['additional_notes']}")

    result = "\n".join(lines).strip()
    return result if result else None


def _build_record_fields(
    *,
    clearance_format: str,
    requester: Dict[str, Any],
    project_context: Dict[str, Any],
    clearance_scope: Dict[str, Any],
    assets_references: Dict[str, Any],
    submission_id: str,
    edit_url: Optional[str],
) -> Dict[str, Any]:
    """Maps payload data to [V2] Clearance Airtable field names."""
    pc = project_context
    cs = clearance_scope
    r = requester
    ar = assets_references

    format_label = FORMAT_LABELS.get(clearance_format, clearance_format)
    project_title = _safe_str(pc.get("project_title"))

    nome_do_case = f"{format_label} - {project_title}" if project_title else format_label

    # For audiovisual, the campaign title is more relevant as the record title
    titulo_campanha = _safe_str(cs.get("product_or_campaign_name") or project_title)

    observacoes = _build_observacoes(
        clearance_format=clearance_format,
        cs=cs,
        pc=pc,
        ar=ar,
    )

    fields: Dict[str, Any] = {
        "Nome do Case": nome_do_case,
        "Clearance Format": clearance_format,
        "Status": "Inbox",
        "Solicitante Nome": _safe_str(r.get("requester_name")),
        "Solicitante Email": _safe_str(r.get("requester_email")),
        "Empresa Solicitante": _safe_str(r.get("requester_company")),
        "Cliente / Contratante": _safe_str(pc.get("client_or_distributor")),
        "Título do Projeto/Campanha": titulo_campanha or project_title,
        "Data de Solicitação": _today_iso(),
        "Canal de Entrada": "Formulário",
        "Airtable Sync Status": "synced",
        "Submission ID": submission_id,
    }

    # Escopo singleSelect
    escopo = FORMAT_ESCOPO.get(clearance_format)
    if escopo:
        fields["Escopo"] = escopo

    # Tipo de Utilizacao singleSelect
    tipo = FORMAT_TIPO_UTILIZACAO.get(clearance_format)
    if tipo:
        fields["Tipo de Utilização"] = tipo

    # Optional fields - only included when truthy
    if cs.get("territory"):
        fields["Território"] = _safe_str(cs["territory"])
    if cs.get("licensing_period"):
        fields["Período de Licenciamento"] = _safe_str(cs["licensing_period"])
    if cs.get("intended_use"):
        fields["Uso Pretendido"] = _safe_str(cs["intended_use"])
    if cs.get("product_or_campaign_name"):
        fields["Marcas / Produto / Campanha"] = _safe_str(cs["product_or_campaign_name"])
    if ar.get("reference_links"):
        fields["Links de Referência"] = _safe_str(ar["reference_links"])
    if observacoes:
        fields["Observações Operacionais"] = observacoes
    if edit_url:
        fields["Edit URL"] = edit_url

    # Drop empty strings (Airtable accepts "" but it is noise)
    fields = {k: v for k, v in fields.items() if v is not None and v != ""}

    return fields


# --- Airtable record creation -------------------------------------------------


def _create_clearance_record(fields: Dict[str, Any]) -> Dict[str, Any]:
    """POSTs a new record to [V2] Clearance and returns the Airtable response."""
    base_id = _base_id()
    table_encoded = quote(CLEARANCE_V2_TABLE, safe="")
    url = f"https://api.airtable.com/v0/{base_id}/{table_encoded}"
    return _request_json("POST", url, payload={"fields": fields})


# --- Public entry point -------------------------------------------------------


def sync_rights_clearance_to_airtable(
    *,
    payload: Any,
    submission_id: str,
    edit_token: str,
) -> Dict[str, Any]:
    """
    Entry point called from submissions.py.

    payload    -- RightsClearanceSubmissionPayload instance.
    edit_token -- raw token used to build the public edit URL.

    Returns:
      {
        "skipped": bool,
        "skip_reason": str | None,
        "airtable_project": dict | None,
        "airtable_tracks": list,
      }
    """
    if not settings.AIRTABLE_RIGHTS_CLEARANCE_MUSICAL_ENABLED:
        logger.info(
            "Rights clearance Airtable sync disabled "
            "(AIRTABLE_RIGHTS_CLEARANCE_MUSICAL_ENABLED=False)"
        )
        return {
            "skipped": True,
            "skip_reason": "feature_flag_disabled",
            "airtable_project": None,
            "airtable_tracks": [],
        }

    clearance_format: str = _safe_str(
        getattr(getattr(payload, "request_type", None), "clearance_format", "")
    )

    if clearance_format not in ACTIVE_FORMATS:
        logger.warning(
            "Rights clearance unknown format=%s -- skipping (submission_id=%s)",
            clearance_format,
            submission_id,
        )
        return {
            "skipped": True,
            "skip_reason": f"unknown_format:{clearance_format}",
            "airtable_project": None,
            "airtable_tracks": [],
        }

    requester = _safe_dict(getattr(payload, "requester_identification", None))
    project_context = _safe_dict(getattr(payload, "project_context", None))
    clearance_scope = _safe_dict(getattr(payload, "clearance_scope", None))
    assets_references = _safe_dict(getattr(payload, "assets_references", None))

    workspace_slug: str = _safe_str(getattr(payload, "workspace_slug", ""))
    edit_url = _build_edit_url(edit_token, workspace_slug)

    fields = _build_record_fields(
        clearance_format=clearance_format,
        requester=requester,
        project_context=project_context,
        clearance_scope=clearance_scope,
        assets_references=assets_references,
        submission_id=submission_id,
        edit_url=edit_url,
    )

    logger.info(
        "Creating [V2] Clearance record: format=%s submission_id=%s nome=%r",
        clearance_format,
        submission_id,
        fields.get("Nome do Case"),
    )

    airtable_record = _create_clearance_record(fields)

    logger.info(
        "Rights clearance [V2] Clearance record created: airtable_id=%s submission_id=%s",
        airtable_record.get("id"),
        submission_id,
    )

    return {
        "skipped": False,
        "skip_reason": None,
        "airtable_project": airtable_record,
        "airtable_tracks": [],
    }
