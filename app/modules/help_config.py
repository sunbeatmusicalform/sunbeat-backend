"""Configuração pública da área de dúvidas por tenant."""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.admin_auth import _admin_token_is_valid
from app.core.database import supabase
from app.modules.admin_config import _read_raw_row
from app.modules.portal_session import require_portal_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspaces", tags=["help-config"])


DEFAULT_TOPICS = [
    {
        "question": "Qual formulário devo usar?",
        "keywords": ["formulário", "formulario", "intake", "clearance", "people", "company"],
        "answer": "Use Lançamentos para singles, EPs, álbuns, faixas, créditos e arquivos; Clearance para pedidos de direitos; Pessoas para artistas, produtores e contatos; e Empresas para pessoas jurídicas e seus responsáveis.",
    },
    {
        "question": "Como os dados são protegidos?",
        "keywords": ["segurança", "seguranca", "proteg", "lgpd", "privacidade", "senha"],
        "answer": "A operação é separada por tenant, o portal exige sessão e o tráfego usa HTTPS. Chaves de integrações ficam protegidas no servidor. Airtable e Drive continuam obedecendo às permissões administradas pelo cliente.",
    },
    {
        "question": "Onde ficam arquivos e cadastros?",
        "keywords": ["airtable", "drive", "arquivo", "cadastro", "pasta"],
        "answer": "Dados estruturados seguem para o Airtable configurado do cliente. Arquivos seguem a lógica de pastas do Google Drive do tenant, organizada por cliente ou artista e por projeto.",
    },
    {
        "question": "Quem consegue acessar?",
        "keywords": ["quem acessa", "quem consegue", "acesso", "compartilh"],
        "answer": "O portal é restrito às pessoas autorizadas pelo cliente. No fluxo técnico, somente os serviços necessários à operação processam os dados, e nenhuma chave de integração é entregue ao navegador.",
    },
]


def _defaults(workspace_slug: str) -> Dict[str, Any]:
    # Atabaque optou por uma apresentação mais enxuta; pode reativar no portal.
    enabled = workspace_slug.strip().lower() != "atabaque"
    return {
        "enabled": enabled,
        "button_label": "Dúvidas?",
        "title": "Assistente da operação",
        "subtitle": "formulários, portal e segurança",
        "welcome_message": "Posso ajudar com os formulários, o portal, integrações e segurança dos dados. O que você precisa?",
        "fallback_message": "Não encontrei uma resposta específica. Fale com a equipe responsável e informe em qual formulário ou etapa surgiu a dúvida.",
        "topics": copy.deepcopy(DEFAULT_TOPICS),
    }


class HelpTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=180)
    answer: str = Field(min_length=1, max_length=2000)
    keywords: List[str] = Field(default_factory=list, max_length=20)


class HelpConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    button_label: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=120)
    subtitle: str = Field(default="", max_length=180)
    welcome_message: str = Field(min_length=1, max_length=1000)
    fallback_message: str = Field(min_length=1, max_length=1000)
    topics: List[HelpTopic] = Field(default_factory=list, max_length=20)


def _stored(workspace_slug: str) -> tuple[Dict[str, Any], bool]:
    row = _read_raw_row(workspace_slug, "release_intake")
    extra = (row or {}).get("extra_settings") or {}
    help_config = extra.get("help") or {}
    return (help_config if isinstance(help_config, dict) else {}), row is not None


def _resolve(workspace_slug: str) -> Dict[str, Any]:
    slug = workspace_slug.strip().lower()
    resolved = _defaults(slug)
    stored, row_exists = _stored(slug)
    for key in resolved:
        if key in stored and stored[key] is not None:
            resolved[key] = copy.deepcopy(stored[key])
    return {
        "ok": True,
        "workspace_slug": slug,
        "row_exists": row_exists,
        **resolved,
    }


async def _require_admin_or_portal(
    workspace_slug: str,
    x_admin_token: Optional[str] = Header(default=None),
    x_portal_token: Optional[str] = Header(default=None),
) -> None:
    if x_admin_token and _admin_token_is_valid(x_admin_token.strip()):
        return
    require_portal_session(workspace_slug, x_portal_token)


@router.get("/{workspace_slug}/help-config")
async def get_help_config(workspace_slug: str) -> Dict[str, Any]:
    return _resolve(workspace_slug)


@router.patch("/{workspace_slug}/help-config")
async def patch_help_config(
    workspace_slug: str,
    body: HelpConfigPatch,
    _: None = Depends(_require_admin_or_portal),
) -> Dict[str, Any]:
    slug = workspace_slug.strip().lower()
    row = _read_raw_row(slug, "release_intake") or {}
    extra = copy.deepcopy(row.get("extra_settings") or {})
    extra["help"] = body.model_dump()
    try:
        supabase.table("workspace_workflow_settings").upsert(
            {
                "workspace_slug": slug,
                "workflow_type": "release_intake",
                "extra_settings": extra,
            },
            on_conflict="workspace_slug,workflow_type",
        ).execute()
    except Exception:
        logger.exception("help_config update failed workspace=%s", slug)
        raise HTTPException(status_code=500, detail="falha ao persistir configuração da área de dúvidas")
    return _resolve(slug)
