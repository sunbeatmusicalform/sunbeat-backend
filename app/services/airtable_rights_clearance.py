from __future__ import annotations

"""
app/services/airtable_rights_clearance.py
──────────────────────────────────────────
Etapa 3 — Syncs Rights Clearance submissions to Airtable:
  [V2] Clearance          (case)
  [V2] Clearance Itens    (1 item per track, Etapa 2)
  [V2] Clearance Partes   (parties per case/item, Etapa 3)

Supported formats (all active):
  music_release_clearance_intake  -> case + itens + partes
  music_project_track             -> case + partes (case-level only)
  audiovisual_product_sync        -> case + partes (case-level only)

Returns:
  {
    "skipped": bool,
    "skip_reason": str | None,
    "airtable_project": dict | None,
    "airtable_tracks": list,
    "airtable_itens": list,
    "airtable_partes": list,
  }
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
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

CLEARANCE_V2_TABLE     = "[V2] Clearance"
CLEARANCE_ITENS_TABLE  = "[V2] Clearance Itens"
CLEARANCE_PARTES_TABLE = "[V2] Clearance Partes"

# Formats that produce [V2] Clearance Itens (one per track)
ITENS_FORMATS = {"music_release_clearance_intake"}

FORMAT_LABELS: Dict[str, str] = {
    "music_release_clearance_intake": "Clearance – Lançamento Musical",
    "music_project_track":             "Clearance – Faixa de Projeto",
    "audiovisual_product_sync":        "Clearance – Sincronização Audiovisual",
}

# Maps format -> Escopo singleSelect value in Airtable
FORMAT_ESCOPO: Dict[str, str] = {
    "music_release_clearance_intake": "musical",
    "music_project_track":             "musical",
    "audiovisual_product_sync":        "nao_musical",
}

# Maps format -> Tipo de Utilizacao singleSelect value in Airtable
FORMAT_TIPO_UTILIZACAO: Dict[str, str] = {
    "music_release_clearance_intake": "Licenciamento",
    "music_project_track":             "Licenciamento",
    "audiovisual_product_sync":        "Sincronização",
}

PARTES_BATCH_SIZE = 10  # Airtable batch-create limit


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
        brand_label = ("Sim — " + brand) if brand else "Sim"
        lines.append(f"[Associação de Marca] {brand_label}")

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


# --- [V2] Clearance Itens (Etapa 2) ------------------------------------------

ITENS_BATCH_SIZE = 10  # Airtable batch-create limit


def _build_item_fields(track: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    """
    Maps a single RightsClearanceTrackPayload dict to [V2] Clearance Itens fields.

    Notes on schema mapping:
    - 'Natureza do Item' valid choices: Obra, Fonograma, Contrato, Autorização,
       Licença, Aprovação, Documento de suporte. We use 'Fonograma' (not 'Faixa'
       which does not exist in the Airtable schema).
    - 'Clearance Case' is multipleRecordLinks -> array of record IDs.
    """
    title = _safe_str(track.get("title"))
    isrc  = _safe_str(track.get("isrc_code"))
    notes = _safe_str(track.get("notes_for_clearance"))

    fields: Dict[str, Any] = {
        "Nome do Item":        title,
        "Clearance Case":      [case_id],
        "Tipo de Direito":     "Fonograma / Master",
        "Natureza do Item":    "Fonograma",
        "Título do Fonograma": title,
        "Status da Liberação": "Pendente",
    }

    # Optional: ISRC only when has_isrc == "yes" and code is present
    if track.get("has_isrc") == "yes" and isrc:
        fields["ISRC"] = isrc

    # Optional: extra notes per track
    parts = []
    if track.get("primary_artists"):
        parts.append(f"[Artistas] {track['primary_artists']}")
    if track.get("authors"):
        parts.append(f"[Autores] {track['authors']}")
    if track.get("publishers"):
        parts.append(f"[Editoras] {track['publishers']}")
    if track.get("phonogram_owner"):
        parts.append(f"[Produtor Fonográfico] {track['phonogram_owner']}")
    if notes:
        parts.append(f"[Notas] {notes}")
    if parts:
        fields["Observações Jurídicas / Operacionais"] = "\n".join(parts)

    # Drop empty strings
    return {k: v for k, v in fields.items() if v is not None and v != ""}


def _create_clearance_itens(
    case_id: str,
    tracks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Batch-creates [V2] Clearance Itens records linked to the given case.
    Returns the list of created Airtable records.
    Airtable allows max 10 records per batch POST.
    """
    if not tracks:
        return []

    base_id = _base_id()
    table_encoded = quote(CLEARANCE_ITENS_TABLE, safe="")
    url = f"https://api.airtable.com/v0/{base_id}/{table_encoded}"

    created: List[Dict[str, Any]] = []
    for i in range(0, len(tracks), ITENS_BATCH_SIZE):
        batch = tracks[i : i + ITENS_BATCH_SIZE]
        records_payload = [
            {"fields": _build_item_fields(t, case_id)} for t in batch
        ]
        response = _request_json("POST", url, payload={"records": records_payload})
        created.extend(response.get("records", []))

    return created


# --- [V2] Clearance Partes (Etapa 3) -----------------------------------------


def _build_parte_fields(
    nome: str,
    papel_no_case: str,
    case_id: str,
    *,
    item_id: Optional[str] = None,
    email: Optional[str] = None,
    observacoes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Builds an Airtable field dict for a single [V2] Clearance Partes record.

    Papel no Case valid choices (confirmed from schema):
      Contratante, Contratada, Cliente, Responsável Legal,
      Responsável Contratual, Responsável Financeiro,
      Artista, Autor, Editora, Gravadora, Selo, Produtora,
      Diretor, Criativo, Outro
    """
    fields: Dict[str, Any] = {
        "Nome da Parte":       nome,
        "Clearance Case":      [case_id],
        "Papel no Case":       papel_no_case,
        "Status de Aprovação": "Pendente",
    }
    if item_id:
        fields["Clearance Item"] = [item_id]
    if email:
        fields["Email de Assinatura"] = email
    if observacoes:
        fields["Observações"] = observacoes

    return {k: v for k, v in fields.items() if v is not None and v != ""}


def _collect_case_partes(
    *,
    clearance_format: str,
    requester: Dict[str, Any],
    project_context: Dict[str, Any],
    clearance_scope: Dict[str, Any],
    case_id: str,
) -> List[Dict[str, Any]]:
    """
    Assembles case-level Partes (no Clearance Item link).

    Always created:
      - Solicitante (requester_name)  -> Responsável Contratual
      - Empresa Solicitante           -> Contratante  (when distinct from name)
      - Cliente / Contratante         -> Cliente

    music_project_track only:
      - artist_name          -> Artista
      - composer_author_info -> Autor
      - publisher_info       -> Editora
      - phonogram_owner      -> Gravadora

    audiovisual_product_sync only:
      - director_name        -> Diretor
    """
    partes: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()

    def _add(
        nome: str,
        papel: str,
        email: Optional[str] = None,
        obs: Optional[str] = None,
    ) -> None:
        nome_s = _safe_str(nome)
        if not nome_s:
            return
        key = (nome_s.lower(), papel)
        if key in seen:
            return
        seen.add(key)
        partes.append(
            _build_parte_fields(nome_s, papel, case_id, email=email, observacoes=obs)
        )

    # -- Solicitante (always present) --
    req_name    = _safe_str(requester.get("requester_name"))
    req_email   = _safe_str(requester.get("requester_email")) or None
    req_company = _safe_str(requester.get("requester_company"))

    if req_name:
        _add(req_name, "Responsável Contratual", email=req_email)

    # Empresa solicitante as separate Contratante entry when distinct from person
    if req_company and req_company.lower() != req_name.lower():
        _add(req_company, "Contratante")

    # -- Cliente / Contratante from project_context --
    client = _safe_str(project_context.get("client_or_distributor"))
    if client:
        _add(client, "Cliente")

    # -- Format-specific case-level parties --
    if clearance_format == "music_project_track":
        _add(_safe_str(clearance_scope.get("artist_name")),          "Artista")
        _add(_safe_str(clearance_scope.get("composer_author_info")), "Autor")
        _add(_safe_str(clearance_scope.get("publisher_info")),       "Editora")
        _add(_safe_str(clearance_scope.get("phonogram_owner")),      "Gravadora")

    if clearance_format == "audiovisual_product_sync":
        _add(_safe_str(clearance_scope.get("director_name")), "Diretor")

    return partes


def _collect_item_partes(
    track: Dict[str, Any],
    case_id: str,
    item_id: str,
) -> List[Dict[str, Any]]:
    """
    Assembles item-level Partes (linked to both Clearance Case and Clearance Item).
    One entry per filled track field: primary_artists, authors, publishers, phonogram_owner.
    """
    partes: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()

    def _add(nome: str, papel: str) -> None:
        nome_s = _safe_str(nome)
        if not nome_s:
            return
        key = (nome_s.lower(), papel)
        if key in seen:
            return
        seen.add(key)
        partes.append(_build_parte_fields(nome_s, papel, case_id, item_id=item_id))

    _add(_safe_str(track.get("primary_artists")), "Artista")
    _add(_safe_str(track.get("authors")),          "Autor")
    _add(_safe_str(track.get("publishers")),        "Editora")
    _add(_safe_str(track.get("phonogram_owner")),   "Gravadora")

    return partes


def _create_clearance_partes(
    partes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Batch-creates [V2] Clearance Partes records."""
    if not partes:
        return []

    base_id = _base_id()
    table_encoded = quote(CLEARANCE_PARTES_TABLE, safe="")
    url = f"https://api.airtable.com/v0/{base_id}/{table_encoded}"

    created: List[Dict[str, Any]] = []
    for i in range(0, len(partes), PARTES_BATCH_SIZE):
        batch = partes[i : i + PARTES_BATCH_SIZE]
        records_payload = [{"fields": f} for f in batch]
        response = _request_json("POST", url, payload={"records": records_payload})
        created.extend(response.get("records", []))

    return created


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
        "airtable_itens": list,
        "airtable_partes": list,
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
            "airtable_itens": [],
            "airtable_partes": [],
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
            "airtable_itens": [],
            "airtable_partes": [],
        }

    requester         = _safe_dict(getattr(payload, "requester_identification", None))
    project_context   = _safe_dict(getattr(payload, "project_context", None))
    clearance_scope   = _safe_dict(getattr(payload, "clearance_scope", None))
    assets_references = _safe_dict(getattr(payload, "assets_references", None))
    tracks_raw: List[Any] = list(getattr(payload, "tracks", None) or [])

    # Pre-convert tracks to plain dicts (needed by both Etapa 2 and Etapa 3)
    tracks_dicts: List[Dict[str, Any]] = [_safe_dict(t) for t in tracks_raw]

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
    case_id: str = airtable_record["id"]

    logger.info(
        "Rights clearance [V2] Clearance record created: airtable_id=%s submission_id=%s",
        case_id,
        submission_id,
    )

    # ---- Etapa 2: [V2] Clearance Itens (music_release_clearance_intake only) ---
    airtable_itens: List[Dict[str, Any]] = []

    if clearance_format in ITENS_FORMATS and tracks_dicts:
        logger.info(
            "Creating %d [V2] Clearance Itens for case=%s submission_id=%s",
            len(tracks_dicts),
            case_id,
            submission_id,
        )
        try:
            airtable_itens = _create_clearance_itens(case_id, tracks_dicts)
            logger.info(
                "[V2] Clearance Itens created: count=%d case=%s submission_id=%s",
                len(airtable_itens),
                case_id,
                submission_id,
            )
        except Exception:
            logger.exception(
                "Failed to create [V2] Clearance Itens for case=%s submission_id=%s -- "
                "case record is intact, Itens will need manual creation",
                case_id,
                submission_id,
            )

    # ---- Etapa 3: [V2] Clearance Partes (all formats) ------------------------
    airtable_partes: List[Dict[str, Any]] = []

    try:
        all_partes: List[Dict[str, Any]] = _collect_case_partes(
            clearance_format=clearance_format,
            requester=requester,
            project_context=project_context,
            clearance_scope=clearance_scope,
            case_id=case_id,
        )

        # Item-level parties: only when format has itens AND creation succeeded.
        # zip stops at the shorter list, so partial item creation is handled safely.
        if clearance_format in ITENS_FORMATS and airtable_itens and tracks_dicts:
            for item_rec, track_dict in zip(airtable_itens, tracks_dicts):
                item_id: str = item_rec["id"]
                all_partes.extend(_collect_item_partes(track_dict, case_id, item_id))

        if all_partes:
            logger.info(
                "Creating %d [V2] Clearance Partes for case=%s submission_id=%s",
                len(all_partes),
                case_id,
                submission_id,
            )
            airtable_partes = _create_clearance_partes(all_partes)
            logger.info(
                "[V2] Clearance Partes created: count=%d case=%s submission_id=%s",
                len(airtable_partes),
                case_id,
                submission_id,
            )

    except Exception:
        # Non-fatal: case and itens are already intact
        logger.exception(
            "Failed to create [V2] Clearance Partes for case=%s submission_id=%s -- "
            "case and itens records are intact, Partes will need manual creation",
            case_id,
            submission_id,
        )

    return {
        "skipped": False,
        "skip_reason": None,
        "airtable_project": airtable_record,
        "airtable_tracks": [],
        "airtable_itens": airtable_itens,
        "airtable_partes": airtable_partes,
    }
