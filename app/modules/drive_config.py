"""Configuração de pastas do Google Drive por workflow, editável pelo workspace.

Rotas:
    GET   /workspaces/{workspace_slug}/workflows/{workflow_type}/drive-config
    PATCH /workspaces/{workspace_slug}/workflows/{workflow_type}/drive-config

O bloco canônico vive em workspace_workflow_settings.extra_settings.drive
(ver docs/workflow-operational-bases.md). Estas rotas expõem uma visão
orientada ao painel do front novo (DriveConfigPanel), sem exigir o contrato
completo de admin_config. Autenticação: mesmo INTERNAL_ADMIN_TOKEN das rotas
internas nesta etapa.

Campos do contrato do painel:
    workflow_type, root_mode, artist_folder_pattern, subfolders,
    overrides, warnings
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.admin_auth import _admin_token_is_valid
from app.core.database import supabase
from app.modules.admin_config import _read_raw_row, _require_admin_token
from app.modules.portal_session import require_portal_session
from app.services.workspace_config import get_workflow_operational_base


async def _require_admin_or_portal(
    workspace_slug: str,
    x_admin_token: Optional[str] = Header(default=None),
    x_portal_token: Optional[str] = Header(default=None),
) -> None:
    """Admin token OU sessão do portal do workspace — 401 caso nenhum valide."""
    if x_admin_token and _admin_token_is_valid(x_admin_token.strip()):
        return
    require_portal_session(workspace_slug, x_portal_token)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["drive-config"])

# Subpastas padrão observadas em produção (app/services/google_drive.py).
_DEFAULT_SUBFOLDERS: Dict[str, List[str]] = {
    "release_intake": ["Audios", "Capa", "Imprensa", "Imagens e Videos", "Outros"],
    "rights_clearance": [],
}

_ROOT_MODE: Dict[str, str] = {
    "release_intake": "mirror_v2_clientes",
    "rights_clearance": "workflow_root",
    "company_registry": "unmapped",
    "people_registry": "unmapped",
}

_ARTIST_FOLDER_PATTERN: Dict[str, Optional[str]] = {
    "release_intake": "Clientes/{Artista}/Projetos",
    "rights_clearance": None,
    "company_registry": None,
    "people_registry": None,
}

_KNOWN_WORKFLOWS = set(_ROOT_MODE)

_FOLDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


class DriveConfigPatch(BaseModel):
    subfolders: Optional[List[str]] = None
    overrides: Optional[Dict[str, Any]] = Field(default=None)
    criar_se_ausente: Optional[bool] = None


def _normalize_slug(value: str) -> str:
    return str(value or "").strip().lower()


def _public_drive_block(drive: Dict[str, Any]) -> Dict[str, Any]:
    """Overrides sem as chaves descritivas (prefixo `_`)."""
    return {k: copy.deepcopy(v) for k, v in (drive or {}).items() if not k.startswith("_")}


def _validate(workflow_type: str, subfolders: List[str], overrides: Dict[str, Any]) -> List[str]:
    """Validações locais, sem chamadas live a Airtable/Drive (safe para painel)."""
    warnings: List[str] = []

    if _ROOT_MODE.get(workflow_type) == "unmapped":
        warnings.append(
            "Este workflow ainda não possui destino operacional de Drive mapeado; "
            "a configuração é apenas declarativa."
        )

    root_id = str(overrides.get("root_folder_id") or "").strip()
    if root_id and not _FOLDER_ID_RE.match(root_id):
        warnings.append("root_folder_id não parece um ID de pasta do Google Drive válido.")

    for key in ("clearance_musical_root_override", "clearance_nonmusical_root_override"):
        value = str(overrides.get(key) or "").strip()
        if value and not _FOLDER_ID_RE.match(value):
            warnings.append(f"{key} não parece um ID de pasta do Google Drive válido.")

    seen = set()
    for name in subfolders:
        key = name.strip().lower()
        if not key:
            warnings.append("Subpasta com nome vazio foi ignorada na validação.")
        elif key in seen:
            warnings.append(f"Subpasta duplicada: {name}")
        seen.add(key)

    return warnings


def _resolve(workspace_slug: str, workflow_type: str) -> Dict[str, Any]:
    row = _read_raw_row(workspace_slug, workflow_type)
    db_drive = ((row or {}).get("extra_settings") or {}).get("drive") or {}
    defaults = get_workflow_operational_base(workflow_type).get("drive") or {}

    merged = copy.deepcopy(defaults)
    merged.update(copy.deepcopy(db_drive))

    overrides = _public_drive_block(merged)
    raw_subfolders = merged.get("subfolders")
    subfolders = (
        [str(s) for s in raw_subfolders]
        if isinstance(raw_subfolders, list)
        else list(_DEFAULT_SUBFOLDERS.get(workflow_type, []))
    )
    overrides.pop("subfolders", None)

    return {
        "ok": True,
        "workspace_slug": workspace_slug,
        "workflow_type": workflow_type,
        "root_mode": _ROOT_MODE.get(workflow_type, "unmapped"),
        "artist_folder_pattern": _ARTIST_FOLDER_PATTERN.get(workflow_type),
        "subfolders": subfolders,
        "overrides": overrides,
        "_origin": "db" if db_drive else "default",
        "warnings": _validate(workflow_type, subfolders, overrides),
    }


@router.get("/{workspace_slug}/workflows/{workflow_type}/drive-config")
async def get_drive_config(
    workspace_slug: str,
    workflow_type: str,
    _: None = Depends(_require_admin_or_portal),
) -> Dict[str, Any]:
    """Visão efetiva da configuração de Drive do workflow (db + defaults)."""
    wf = _normalize_slug(workflow_type)
    if wf not in _KNOWN_WORKFLOWS:
        raise HTTPException(status_code=404, detail=f"workflow desconhecido: {workflow_type}")
    return _resolve(_normalize_slug(workspace_slug), wf)


@router.patch("/{workspace_slug}/workflows/{workflow_type}/drive-config")
async def patch_drive_config(
    workspace_slug: str,
    workflow_type: str,
    body: DriveConfigPatch,
    _: None = Depends(_require_admin_or_portal),
) -> Dict[str, Any]:
    """Deep-merge no bloco extra_settings.drive. Campos omitidos não são gravados."""
    wf = _normalize_slug(workflow_type)
    if wf not in _KNOWN_WORKFLOWS:
        raise HTTPException(status_code=404, detail=f"workflow desconhecido: {workflow_type}")
    slug = _normalize_slug(workspace_slug)

    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return _resolve(slug, wf)

    row = _read_raw_row(slug, wf) or {}
    extra = copy.deepcopy(row.get("extra_settings") or {})
    drive = copy.deepcopy(extra.get("drive") or {})

    if "subfolders" in fields and body.subfolders is not None:
        drive["subfolders"] = [s.strip() for s in body.subfolders if s.strip()]
    if "criar_se_ausente" in fields:
        drive["criar_se_ausente"] = bool(body.criar_se_ausente)
    if "overrides" in fields and isinstance(body.overrides, dict):
        for key, value in body.overrides.items():
            if str(key).startswith("_"):
                continue  # chaves descritivas não são editáveis pelo painel
            drive[str(key)] = value

    extra["drive"] = drive

    payload = {
        "workspace_slug": slug,
        "workflow_type": wf,
        "post_submit_email_enabled": row.get("post_submit_email_enabled", True),
        "edit_email_enabled": row.get("edit_email_enabled", True),
        "airtable_sync_enabled": row.get("airtable_sync_enabled", True),
        "drive_sync_enabled": row.get("drive_sync_enabled", False),
        "extra_settings": extra,
    }

    try:
        supabase.table("workspace_workflow_settings").upsert(
            payload, on_conflict="workspace_slug,workflow_type"
        ).execute()
    except Exception as exc:
        logger.error(
            "drive_config: falha ao persistir workspace=%s workflow=%s: %s", slug, wf, exc
        )
        raise HTTPException(status_code=500, detail="falha ao persistir drive-config")

    resolved = _resolve(slug, wf)
    resolved["updated"] = sorted(fields.keys())
    return resolved
