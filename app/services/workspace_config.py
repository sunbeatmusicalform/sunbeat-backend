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

import copy
import logging
from typing import Any, Dict, Optional

from app.core.database import supabase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base operacional por workflow.
#
# Mantem o mapa dentro da estrutura ja existente de extra_settings para que a
# configuracao manual e a futura Setup AI escrevam no mesmo lugar.
# Chaves com prefixo "_" sao metadados de leitura; os servicos de sync seguem
# consumindo apenas os overrides ja existentes.
# ---------------------------------------------------------------------------
_OPERATIONAL_EXTRA_SETTINGS: Dict[str, Dict[str, Any]] = {
    "release_intake": {
        "operational_base": {
            "primary_store": "supabase",
            "tables": ["submissions", "tracks", "submissions_revisions"],
            "service": "app.modules.submissions",
        },
        "airtable": {
            "_target_label": "[V2] Projetos Musicais + [V2] Faixas Musicais",
            "_service": "app.services.airtable",
            "_settings_keys": [
                "AIRTABLE_BASE_ID",
                "AIRTABLE_PROJECTS_TABLE",
                "AIRTABLE_TRACKS_TABLE",
            ],
            "base_id_override": None,
            "projects_table_override": None,
            "tracks_table_override": None,
        },
        "drive": {
            "_target_label": "Workspace/root folder + routing por cliente",
            "_service": "app.services.google_drive.sync_submission_to_google_drive",
            "_settings_keys": ["GOOGLE_DRIVE_ROOT_FOLDER_ID"],
        },
    },
    "rights_clearance": {
        "operational_base": {
            "primary_store": "supabase",
            "tables": ["submissions", "tracks", "submissions_revisions"],
            "service": "app.modules.submissions",
        },
        "airtable": {
            "_target_label": "[V2] Clearance + [V2] Clearance Itens + [V2] Clearance Partes",
            "_service": "app.services.airtable_rights_clearance",
            "_settings_keys": ["AIRTABLE_BASE_ID"],
            "base_id_override": None,
            "clearance_case_table_override": None,
            "clearance_itens_table_override": None,
            "clearance_partes_table_override": None,
            "people_invite_auto_create_enabled": False,
            "people_invite_default_expiration_days": 14,
        },
        "drive": {
            "_target_label": "Clearance musical/nao-musical root folders",
            "_service": "app.services.google_drive.sync_clearance_to_google_drive",
            "_settings_keys": [
                "GOOGLE_DRIVE_CLEARANCE_MUSICAL_ROOT_FOLDER_ID",
                "GOOGLE_DRIVE_CLEARANCE_NON_MUSICAL_ROOT_FOLDER_ID",
                "GOOGLE_DRIVE_ROOT_FOLDER_ID",
            ],
            "clearance_musical_root_override": None,
            "clearance_nonmusical_root_override": None,
        },
    },
    "company_registry": {
        "operational_base": {
            "primary_store": "supabase",
            "tables": ["submissions", "submissions_revisions"],
            "service": "app.modules.submissions",
        },
        "airtable": {
            "_target_label": "[V2] - Empresas",
            "_service": "app.services.airtable_company_registry",
            "_settings_keys": [
                "AIRTABLE_BASE_ID",
                "AIRTABLE_COMPANY_REGISTRY_TABLE_ID",
            ],
            "base_id_override": None,
            "company_registry_table_override": "[V2] - Empresas",
        },
        "drive": {
            "_target_label": "Nao mapeado para sync operacional nesta etapa",
            "_service": None,
            "_settings_keys": [],
        },
    },
    "people_registry": {
        "operational_base": {
            "primary_store": "supabase",
            "tables": ["people_registry_records"],
            "service": "app.services.people_registry",
        },
        "airtable": {
            "_target_label": "[V2] - Pessoas",
            "_service": "app.services.people_registry_airtable_sync",
            "_settings_keys": [
                "AIRTABLE_PEOPLE_REGISTRY_BASE_ID",
                "AIRTABLE_BASE_ID",
                "AIRTABLE_PEOPLE_REGISTRY_ATABAQUE_TABLE",
            ],
            "base_id_override": None,
            "people_registry_table_override": "[V2] - Pessoas",
            "people_registry_legacy_table_override": "Dados Cadastrais",
        },
        "drive": {
            "_target_label": "Nao mapeado para sync operacional nesta etapa",
            "_service": None,
            "_settings_keys": [],
        },
    },
}

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
        "extra_settings": _OPERATIONAL_EXTRA_SETTINGS["release_intake"],
    },
    "rights_clearance": {
        "post_submit_email_enabled": True,
        "edit_email_enabled": True,
        "airtable_sync_enabled": True,
        "drive_sync_enabled": True,
        "edit_mode_enabled": True,
        "extra_settings": _OPERATIONAL_EXTRA_SETTINGS["rights_clearance"],
    },
    "company_registry": {
        "post_submit_email_enabled": True,
        "edit_email_enabled": True,
        "airtable_sync_enabled": True,
        # drive_sync_enabled=False: pastas especificas ainda a configurar no Fly.io
        "drive_sync_enabled": False,
        "edit_mode_enabled": True,
        "extra_settings": _OPERATIONAL_EXTRA_SETTINGS["company_registry"],
    },
    "people_registry": {
        # Email gerido pela arquitetura propria do people_registry (nao por submissions.py)
        "post_submit_email_enabled": False,
        "edit_email_enabled": False,
        "airtable_sync_enabled": True,
        "drive_sync_enabled": False,
        "edit_mode_enabled": True,
        "extra_settings": _OPERATIONAL_EXTRA_SETTINGS["people_registry"],
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


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _get_defaults(workflow_type: str) -> Dict[str, Any]:
    return copy.deepcopy(_WORKFLOW_DEFAULTS.get(workflow_type, _UNKNOWN_WORKFLOW_DEFAULT))


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
            if field == "extra_settings" and isinstance(row[field], dict):
                effective[field] = _deep_merge_dict(
                    effective.get("extra_settings") or {},
                    row[field],
                )
            else:
                effective[field] = row[field]

    logger.debug(
        "workspace_config: config carregada workspace=%s workflow=%s",
        workspace_slug,
        workflow_type,
    )
    return effective


def get_workflow_operational_base(workflow_type: str) -> Dict[str, Any]:
    """
    Retorna o mapa operacional herdavel de um workflow.

    Nao le o banco: serve como baseline estrutural para documentacao,
    validacoes internas e futuras gravacoes manuais/Setup AI em extra_settings.
    """
    extra_settings = _get_defaults(workflow_type).get("extra_settings") or {}
    return {
        "operational_base": copy.deepcopy(extra_settings.get("operational_base") or {}),
        "airtable": copy.deepcopy(extra_settings.get("airtable") or {}),
        "drive": copy.deepcopy(extra_settings.get("drive") or {}),
    }


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


def get_email_event_config(
    workspace_slug: str,
    workflow_type: str,
    event: str,
    *,
    variant: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retorna {"recipients": [...], "enabled": bool} para um evento de email.

    Resolucao em cascata:
      1. extra_settings.email.variants[variant][event]  (se variant fornecida)
      2. extra_settings.email.events[event]
      3. {"recipients": [], "enabled": False}           (nao configurado)

    Limita recipients a 5 enderecos. Nunca lanca excecao.
    """
    try:
        email_extra = get_email_extra_config(workspace_slug, workflow_type)
        events: Dict[str, Any] = email_extra.get("events") or {}
        variants: Dict[str, Any] = email_extra.get("variants") or {}

        # 1. Variant override
        if variant and variant in variants:
            variant_events = variants[variant] or {}
            if event in variant_events:
                cfg = variant_events[event] or {}
                return {
                    "recipients": list((cfg.get("recipients") or []))[:5],
                    "enabled": bool(cfg.get("enabled", True)),
                }

        # 2. Base events block
        if event in events:
            cfg = events[event] or {}
            return {
                "recipients": list((cfg.get("recipients") or []))[:5],
                "enabled": bool(cfg.get("enabled", True)),
            }

        # 3. Nao configurado
        return {"recipients": [], "enabled": False}
    except Exception:
        return {"recipients": [], "enabled": False}
