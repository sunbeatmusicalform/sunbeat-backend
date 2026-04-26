from __future__ import annotations

"""
app/services/airtable_rights_clearance.py
──────────────────────────────────────────
Syncs Rights Clearance submissions to Airtable.

Routing by clearance_format:
  music_release_clearance_intake → v2 Projetos Musicais  (project + tracks)
  music_project_track            → v2 Projetos Musicais  (project only)
  audiovisual_product_sync       → skipped (future v2 Clearance table)

Returns a dict:
  {
    "skipped": True | False,
    "skip_reason": str | None,
    "airtable_project": dict | None,
    "airtable_tracks": list | None,
  }
"""

import logging
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services.airtable import (
    create_airtable_project,
    create_airtable_tracks,
    update_airtable_project_focus_track,
)

logger = logging.getLogger(__name__)

# Formats that map to v2 Projetos Musicais
MUSICAL_FORMATS = {"music_release_clearance_intake", "music_project_track"}

# Formats deferred to future v2 Clearance table
DEFERRED_FORMATS = {"audiovisual_product_sync"}


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _safe_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _assemble_clearance_notes(
    clearance_format: str,
    clearance_scope: Any,
    project_context: Any,
    assets_references: Any,
) -> Optional[str]:
    """
    Builds the 'Observações do Projeto' text block from clearance-specific fields.
    Returns None if nothing meaningful is present.
    """
    lines: List[str] = []

    cs = _safe_model_dict(clearance_scope)
    pc = _safe_model_dict(project_context)
    ar = _safe_model_dict(assets_references)

    if clearance_format:
        format_label = {
            "music_release_clearance_intake": "Clearance – Lançamento Musical",
            "music_project_track": "Clearance – Faixa de Projeto",
            "audiovisual_product_sync": "Clearance – Sincronização Audiovisual",
        }.get(clearance_format, clearance_format)
        lines.append(f"[Tipo de Clearance] {format_label}")

    # clearance_scope fields
    if cs.get("music_title"):
        lines.append(f"[Música] {cs['music_title']}")
    if cs.get("artist_name"):
        lines.append(f"[Artista] {cs['artist_name']}")
    if cs.get("phonogram_owner"):
        lines.append(f"[Produtor Fonográfico] {cs['phonogram_owner']}")
    if cs.get("composer_author_info"):
        lines.append(f"[Compositores / Autores] {cs['composer_author_info']}")
    if cs.get("publisher_info"):
        lines.append(f"[Editoras] {cs['publisher_info']}")
    if cs.get("territory"):
        lines.append(f"[Território] {cs['territory']}")
    if cs.get("licensing_period"):
        lines.append(f"[Período de Licenciamento] {cs['licensing_period']}")
    if cs.get("material_type"):
        lines.append(f"[Material / Direitos] {cs['material_type']}")
    if cs.get("intended_use"):
        lines.append(f"[Uso Pretendido] {cs['intended_use']}")
    if cs.get("exclusivity"):
        lines.append(f"[Exclusividade] {cs['exclusivity']}")
    if cs.get("media_channels"):
        lines.append(f"[Canais / Mídias] {cs['media_channels']}")

    # project_context extra notes
    if pc.get("project_synopsis"):
        lines.append(f"[Sinopse] {pc['project_synopsis']}")
    if pc.get("general_clearance_notes"):
        lines.append(f"[Notas Gerais] {pc['general_clearance_notes']}")
    if pc.get("has_brand_association") and str(pc["has_brand_association"]).lower() in (
        "sim", "yes", "true",
    ):
        brand_ctx = pc.get("brand_context") or ""
        brand_note = f"Sim — {brand_ctx}" if brand_ctx else "Sim"
        lines.append(f"[Associação de Marca] {brand_note}")

    # assets_references
    if ar.get("reference_links"):
        lines.append(f"[Links de Referência] {ar['reference_links']}")
    if ar.get("additional_notes"):
        lines.append(f"[Notas Adicionais] {ar['additional_notes']}")

    result = "\n".join(lines).strip()
    return result if result else None


def _safe_model_dict(obj: Any) -> Dict[str, Any]:
    """Converts a pydantic model or plain dict to a plain dict safely."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump() or {}
    if hasattr(obj, "dict"):
        return obj.dict() or {}
    return {}


def _build_identification_dict(requester: Any, project_context: Any) -> Dict[str, Any]:
    r = _safe_model_dict(requester)
    pc = _safe_model_dict(project_context)
    return {
        "project_title": pc.get("project_title") or "",
        "release_type": pc.get("release_type") or "Clearance",
        "submitter_name": r.get("requester_name") or "",
        "submitter_email": r.get("requester_email") or "",
    }


def _build_project_dict(project_context: Any) -> Dict[str, Any]:
    pc = _safe_model_dict(project_context)
    return {
        "release_date": pc.get("release_or_start_date") or "",
        "genre": pc.get("client_or_distributor") or "",
    }


def _build_marketing_dict(
    clearance_format: str,
    clearance_scope: Any,
    project_context: Any,
    assets_references: Any,
) -> Dict[str, Any]:
    notes = _assemble_clearance_notes(
        clearance_format=clearance_format,
        clearance_scope=clearance_scope,
        project_context=project_context,
        assets_references=assets_references,
    )
    return {"general_notes": notes}


def _build_track_rows(tracks: List[Any]) -> List[Dict[str, Any]]:
    """
    Maps RightsClearance track objects to the dict shape expected by
    create_airtable_tracks (which mirrors the release_intake track schema).
    """
    rows: List[Dict[str, Any]] = []
    for i, track in enumerate(tracks, start=1):
        t = _safe_model_dict(track) if not isinstance(track, dict) else track
        rows.append(
            {
                "title": t.get("title") or "",
                "order_number": t.get("order_number") or i,
                "artists": t.get("primary_artists") or t.get("artists") or "",
                "authors": t.get("authors") or "",
                "publishers": t.get("publishers") or "",
                "phonographic_producer": (
                    t.get("phonogram_owner") or t.get("phonographic_producer") or ""
                ),
                "has_isrc": t.get("has_isrc") or "",
                "isrc": t.get("isrc_code") or t.get("isrc") or "",
                # store clearance notes in the lyrics field so they appear in Airtable
                "lyrics": t.get("notes_for_clearance") or "",
            }
        )
    return rows


# ─── Public API ───────────────────────────────────────────────────────────────


def sync_rights_clearance_to_airtable(
    *,
    payload: Any,
    submission_id: str,
    edit_token: str,
) -> Dict[str, Any]:
    """
    Entry point called from submissions.py.

    payload is a RightsClearanceSubmissionPayload instance.

    Returns:
      {
        "skipped": bool,
        "skip_reason": str | None,
        "airtable_project": dict | None,
        "airtable_tracks": list | None,
      }
    """
    if not settings.AIRTABLE_RIGHTS_CLEARANCE_MUSICAL_ENABLED:
        logger.info(
            "Rights clearance Airtable sync disabled (AIRTABLE_RIGHTS_CLEARANCE_MUSICAL_ENABLED=False)"
        )
        return {"skipped": True, "skip_reason": "feature_flag_disabled", "airtable_project": None, "airtable_tracks": None}

    clearance_format: str = _safe_str(
        getattr(getattr(payload, "request_type", None), "clearance_format", "")
    )

    # Deferred formats — future v2 Clearance table
    if clearance_format in DEFERRED_FORMATS:
        logger.info(
            "Rights clearance format=%s deferred to v2 Clearance table (submission_id=%s)",
            clearance_format,
            submission_id,
        )
        return {
            "skipped": True,
            "skip_reason": "deferred_to_v2_clearance",
            "airtable_project": None,
            "airtable_tracks": None,
        }

    if clearance_format not in MUSICAL_FORMATS:
        logger.warning(
            "Rights clearance unknown format=%s — skipping (submission_id=%s)",
            clearance_format,
            submission_id,
        )
        return {
            "skipped": True,
            "skip_reason": f"unknown_format:{clearance_format}",
            "airtable_project": None,
            "airtable_tracks": None,
        }

    requester = getattr(payload, "requester_identification", None)
    project_context = getattr(payload, "project_context", None)
    clearance_scope = getattr(payload, "clearance_scope", None)
    assets_references = getattr(payload, "assets_references", None)
    tracks_raw: List[Any] = list(getattr(payload, "tracks", None) or [])

    identification = _build_identification_dict(requester, project_context)
    project = _build_project_dict(project_context)
    marketing = _build_marketing_dict(
        clearance_format=clearance_format,
        clearance_scope=clearance_scope,
        project_context=project_context,
        assets_references=assets_references,
    )

    logger.info(
        "Syncing rights clearance to Airtable: format=%s submission_id=%s title=%r",
        clearance_format,
        submission_id,
        identification.get("project_title"),
    )

    airtable_project = create_airtable_project(
        workspace_slug=getattr(payload, "workspace_slug", ""),
        identification=identification,
        project=project,
        marketing=marketing,
        submission_id=submission_id,
        draft_token=None,
        edit_url=None,
    )

    airtable_project_id: str = airtable_project["id"]
    airtable_tracks: List[Dict[str, Any]] = []

    if clearance_format == "music_release_clearance_intake" and tracks_raw:
        track_rows = _build_track_rows(tracks_raw)
        if track_rows:
            airtable_tracks = create_airtable_tracks(
                airtable_project_id=airtable_project_id,
                workspace_slug=getattr(payload, "workspace_slug", ""),
                submission_id=submission_id,
                tracks=track_rows,
            )

            # Mark the first track as focus track
            if airtable_tracks:
                try:
                    update_airtable_project_focus_track(
                        airtable_project_id=airtable_project_id,
                        airtable_focus_track_id=airtable_tracks[0]["id"],
                    )
                except Exception:
                    logger.exception(
                        "Focus track update failed for clearance project_id=%s",
                        airtable_project_id,
                    )

    logger.info(
        "Rights clearance Airtable sync complete: format=%s project_id=%s tracks=%d",
        clearance_format,
        airtable_project_id,
        len(airtable_tracks),
    )

    return {
        "skipped": False,
        "skip_reason": None,
        "airtable_project": airtable_project,
        "airtable_tracks": airtable_tracks,
    }
