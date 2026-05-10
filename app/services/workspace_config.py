"""
workspace_config.py
Servico de leitura de configuracao operacional por workspace + workflow type.

Fonte: tabela `workspace_workflow_settings` no Supabase.
Fallback: defaults hardcoded que reproduzem o comportamento atual de cada workflow.
           Garante que a ausencia de config nao altera comportamento em producao.

Uso:
    from app.services.workspace_config import get_workflow_settings
    settings = get_workflow_settings("atabaque", "company_registry")
    if settings["post_submit_email_enabled"]:
        ...
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.core.database import supabase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults por workflow — reproduzem o comportamento em producao em 07/05/2026.
# Usado como fallback quando nao existe linha em workspace_workflow_settings.
# ---------------------------------------------------------------------------
_WORKFLOW_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "release_intake": {
        "post_submit_email_enabled": True,
        "edit_email_enabled": True,
        "airtable_sync_enabled": True,
        "drive_sync_enabled": True,
        "edit_mode_enabled": True,
        "extra_settings": {},
    },
    "rights_clearance": {
        "post_submit_email_enabled": True,
        "edit_email_enabled": True,
        "airtable_sync_enabled": True,
        "drive_sync_enabled": True,
        "edit_mode_enabled": True,
        "extra_settings": {},
    },
    "company_registry": {
        "post_submit_email_enabled": True,
        "edit_email_enabled": True,
        "airtable_sync_enabled": True,
        # drive_sync_enabled=False: pastas especificas ainda a configurar no Fly.io
        "drive_sync_enabled": False,
        "edit_mode_enabled": True,
        "extra_settings": {},
    },
    "people_registry": {
        # Email gerido pela arquitetura propria do people_registry (nao por submissions.py)
        "post_submit_email_enabled": False,
        "edit_email_enabled": False,
        "airtable_sync_enabled": True,
        "drive_sync_enabled": False,
        "edit_mode_enabled": True,
        "extra_settings": {},
    },
}

# Fallback global para workflows desconhecidos (custom / futuro)
_UNKNOWN_WORKFLOW_DEFAULT: Dict[str, Any] = {
    "post_submit_email_enabled": False,
    "edit_email_enabled": False,
    "airtable_sync_enabled": False,
    "drive_sync_enabled": False,
    "edit_mode_enabled": False,
    "extra_settings": {},
}

_SETTINGS_FIELDS = [
    "post_submit_email_enabled",
    "edit_email_enabled",
    "airtable_sync_enabled",
    "drive_sync_enabled",
    "edit_mode_enabled",
    "extra_settings",
]


def _get_defaults(workflow_type: str) -> Dict[str, Any]:
    return dict(_WORKFLOW_DEFAULTS.get(workflow_type, _UNKNOWN_WORKFLOW_DEFAULT))


def get_workflow_settings(
    workspace_slug: str,
    workflow_type: str,
) -> Dict[str, Any]:
    """
    Retorna a configuracao operacional efetiva para um par workspace + workflow.

    Resolucao (ordem de prioridade):
      1. Linha em `workspace_workflow_settings` para (workspace_slug, workflow_type)
      2. Defaults hardcoded em _WORKFLOW_DEFAULTS para o workflow_type
      3. _UNKNOWN_WORKFLOW_DEFAULT para workflows nao mapeados

    Nunca lanca excecao — em caso de erro de banco retorna defaults e loga warning.
    """
    try:
        result = (
            supabase.table("workspace_workflow_settings")
            .select(", ".join(_SETTINGS_FIELDS))
            .eq("workspace_slug", workspace_slug)
            .eq("workflow_type", workflow_type)
            .limit(1)
            .execute()
        )
        rows = result.data or []
    except Exception as exc:
        logger.warning(
            "workspace_config: erro ao ler workspace_workflow_settings "
            "workspace=%s workflow=%s — usando defaults. erro=%s",
            workspace_slug,
            workflow_type,
            exc,
        )
        return _get_defaults(workflow_type)

    if not rows:
        logger.debug(
            "workspace_config: sem config para workspace=%s workflow=%s — usando defaults.",
            workspace_slug,
            workflow_type,
        )
        return _get_defaults(workflow_type)

    row = rows[0]
    # Mescla defaults com o que veio do banco (protege contra colunas futuras ausentes)
    effective = _get_defaults(workflow_type)
    for field in _SETTINGS_FIELDS:
        if field in row and row[field] is not None:
            effective[field] = row[field]

    logger.debug(
        "workspace_config: config carregada workspace=%s workflow=%s",
        workspace_slug,
        workflow_type,
    )
    return effective


def get_workflow_setting(
    workspace_slug: str,
    workflow_type: str,
    key: str,
    *,
    default: Optional[Any] = None,
) -> Any:
    """
    Atalho para ler um unico campo de configuracao.

    Exemplo:
        if get_workflow_setting("atabaque", "company_registry", "post_submit_email_enabled"):
            send_email(...)
    """
    settings = get_workflow_settings(workspace_slug, workflow_type)
    return settings.get(key, default)

def get_airtable_extra_config(
    workspace_slug: str,
    workflow_type: str,
) -> Dict[str, Any]:
    """
    Retorna o bloco extra_settings.airtable para um par workspace + workflow.
    Fallback seguro para {} se o campo estiver ausente, nulo ou mal-formado.
    """
    try:
        settings = get_workflow_settings(workspace_slug, workflow_type)
        return (settings.get("extra_settings") or {}).get("airtable") or {}
    except Exception:
        return {}


def get_drive_extra_config(
    workspace_slug: str,
    workflow_type: str,
) -> Dict[str, Any]:
    """
    Retorna o bloco extra_settings.drive para um par workspace + workflow.
    Fallback seguro para {} se o campo estiver ausente, nulo ou mal-formado.
    """
    try:
        settings = get_workflow_settings(workspace_slug, workflow_type)
        return (settings.get("extra_settings") or {}).get("drive") or {}
    except Exception:
        return {}


def get_email_extra_config(
    workspace_slug: str,
    workflow_type: str,
) -> Dict[str, Any]:
    """
    Retorna o bloco extra_settings.email para um par workspace + workflow.
    Fallback seguro para {} se o campo estiver ausente, nulo ou mal-formado.
    """
    try:
        settings = get_workflow_settings(workspace_slug, workflow_type)
        return (settings.get("extra_settings") or {}).get("email") or {}
    except Exception:
        return {}
