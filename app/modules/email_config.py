"""Configuração self-service de e-mails por workspace e workflow.

GET/PATCH /workspaces/{workspace_slug}/workflows/{workflow_type}/email-config

O contrato persiste somente em ``workspace_workflow_settings.extra_settings.email``
e aceita sessão do portal do próprio workspace ou token administrativo.
"""
from __future__ import annotations

import copy
import logging
import re
from html import escape
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.admin_auth import _admin_token_is_valid
from app.core.database import supabase
from app.modules.admin_config import _read_raw_row
from app.modules.portal_session import require_portal_session
from app.services.edit_access import DEFAULT_EDIT_POLICIES, EDIT_POLICIES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspaces", tags=["email-config"])

EMAIL_EVENTS = ("on_draft", "on_submit", "on_edit", "on_first_stage", "on_summary")
SUPPORTED_PLACEHOLDERS = (
    "submitter_name",
    "submitter_email",
    "project_title",
    "release_date",
    "release_type",
    "genre",
    "primary_artist",
    "draft_link",
    "edit_link",
    "workspace_name",
    "current_step",
    "tracks_count",
    "focus_track",
    "track_titles",
)

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")
_UNSAFE_HTML_RE = re.compile(
    r"<\s*script\b|javascript\s*:|\bon(?:error|load|click|mouseover|focus)\s*=",
    re.IGNORECASE,
)


def _wrap_preview_html(content: str) -> str:
    return (
        '<div style="font-family: Arial, sans-serif; line-height: 1.6; '
        f'color: #111827;">{content}</div>'
    )


def _default_template_preview(
    workspace_slug: str,
    workflow_type: str,
    event: str,
    edit_policy: str,
) -> Dict[str, str]:
    """Return the current runtime default populated with representative data.

    These values are response-only: an empty persisted template continues to mean
    "use the backend default".  The portal can therefore show what will actually
    be sent without copying the default into tenant configuration.
    """
    workspace_name = workspace_slug.replace("-", " ").title()
    project_title = "Novo Horizonte"
    submitter_name = "Ana Souza"
    submitter_email = "ana@exemplo.com"
    draft_link = f"https://sunbeat.pro/intake/{workspace_slug}?draft=abc123"
    edit_link = f"https://sunbeat.pro/intake/{workspace_slug}?edit_token=abc123"

    if event == "on_draft":
        return {
            "subject": "Continue o preenchimento do seu rascunho",
            "body": _wrap_preview_html(
                f"<p>Ola, {escape(submitter_name)}!</p>"
                f"<p>Seu rascunho para <strong>{escape(project_title)}</strong> foi salvo.</p>"
                "<p>Voce pode continuar o preenchimento pelo link abaixo:</p>"
                f'<p><a href="{draft_link}">{draft_link}</a></p>'
                "<p>Esse link leva voce de volta ao formulario com seu rascunho carregado.</p>"
            ),
        }

    if event == "on_first_stage" and workflow_type == "release_intake":
        return {
            "subject": f"Primeira etapa concluida - {project_title}",
            "body": _wrap_preview_html(
                f"<p>O intake da <strong>{escape(workspace_name)}</strong> recebeu a conclusao da primeira etapa do formulario.</p>"
                '<table style="border-collapse: collapse; width: 100%; margin: 24px 0;"><tbody>'
                f'<tr><td>Projeto</td><td><strong>{escape(project_title)}</strong></td></tr>'
                f'<tr><td>Responsavel</td><td>{escape(submitter_name)}</td></tr>'
                f'<tr><td>E-mail</td><td>{escape(submitter_email)}</td></tr>'
                '<tr><td>Etapa atual</td><td>Projeto</td></tr>'
                "</tbody></table>"
                "<p>Para continuar o atendimento operacional ou retomar o rascunho, use o link abaixo:</p>"
                f'<p><a href="{draft_link}">{draft_link}</a></p>'
            ),
        }

    if event == "on_summary" and workflow_type == "release_intake":
        return {
            "subject": f"Nova submissao recebida - {project_title}",
            "body": _wrap_preview_html(
                f"<p>O intake da <strong>{escape(workspace_name)}</strong> recebeu uma nova submissao.</p>"
                '<table style="border-collapse: collapse; width: 100%; margin: 24px 0;"><tbody>'
                f'<tr><td>Projeto</td><td><strong>{escape(project_title)}</strong></td></tr>'
                f'<tr><td>Responsavel</td><td>{escape(submitter_name)}</td></tr>'
                f'<tr><td>E-mail</td><td>{escape(submitter_email)}</td></tr>'
                '<tr><td>Tipo de lancamento</td><td>Single</td></tr>'
                '<tr><td>Data prevista</td><td>18/09/2026</td></tr>'
                '<tr><td>Genero</td><td>MPB</td></tr>'
                f'<tr><td>Faixa foco</td><td>{escape(project_title)}</td></tr>'
                "</tbody></table>"
                f"<p><strong>Faixas enviadas</strong></p><ul><li>{escape(project_title)}</li></ul>"
                "<p>Para revisar ou ajustar a submissao, use o link abaixo:</p>"
                f'<p><a href="{edit_link}">{edit_link}</a></p>'
            ),
        }

    if event in {"on_submit", "on_edit"}:
        include_edit_link = (
            event == "on_edit"
            or edit_policy == "link_after_submit"
        )
        edit_paragraph = (
            "<p>Se precisar revisar ou atualizar as informações enviadas, use o link abaixo:</p>"
            f'<p><a href="{edit_link}">{edit_link}</a></p>'
            if include_edit_link
            else "<p>Após o envio, alterações precisam ser autorizadas pela equipe responsável.</p>"
        )
        if workflow_type == "rights_clearance":
            subject = f"Solicitacao de clearance recebida - {project_title}"
            description = (
                f"Recebemos a sua solicitacao de clearance para <strong>{escape(project_title)}</strong>."
                "</p><p>Nossa equipe vai analisar as informacoes enviadas e entrar em contato "
                "se precisar de algo adicional."
            )
        elif workflow_type in {"company_registry", "people_registry"}:
            subject = f"Cadastro recebido - {project_title}"
            description = (
                f"Recebemos o cadastro de <strong>{escape(project_title)}</strong>."
                "</p><p>Nossa equipe vai revisar as informacoes e entrar em contato "
                "se precisar de algo adicional."
            )
        else:
            subject = f"Resumo do lançamento - {project_title} - 18/09/2026 + Banda Horizonte"
            description = (
                f"Recebemos o envio do lancamento <strong>{escape(project_title)}</strong>."
                "</p><p>A data informada para o lancamento e <strong>18/09/2026</strong>."
                "</p><p>Faltam <strong>47</strong> dias para o lancamento."
            )
        return {
            "subject": subject,
            "body": _wrap_preview_html(
                f"<p>Ola, {escape(submitter_name)}!</p><p>Obrigada pelo envio.</p>"
                f"<p>{description}</p>{edit_paragraph}"
                "<p>Se voce nao reconhece este envio, pode ignorar este email.</p>"
            ),
        }

    return {"subject": "", "body": ""}


def _normalize_email_list(values: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip().lower()
        if not value:
            continue
        if not _EMAIL_RE.match(value):
            raise ValueError(f"endereço de e-mail inválido: {value}")
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    if len(normalized) > 5:
        raise ValueError("são permitidos no máximo 5 endereços")
    return normalized


def _validate_template_text(value: str, *, is_body: bool) -> str:
    value = str(value or "").strip()
    if not is_body and ("\n" in value or "\r" in value):
        raise ValueError("o assunto não pode conter quebras de linha")
    if is_body and _UNSAFE_HTML_RE.search(value):
        raise ValueError("o template contém HTML não permitido")
    unknown = sorted(set(_PLACEHOLDER_RE.findall(value)) - set(SUPPORTED_PLACEHOLDERS))
    if unknown:
        raise ValueError(f"placeholders desconhecidos: {', '.join(unknown)}")
    return value


class EmailEventConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    recipients: List[str] = Field(default_factory=list, max_length=5)

    @field_validator("recipients")
    @classmethod
    def validate_recipients(cls, value: List[str]) -> List[str]:
        return _normalize_email_list(value)


class EmailTemplateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=50_000)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        return _validate_template_text(value, is_body=False)

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        return _validate_template_text(value, is_body=True)


class EmailConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: Optional[Dict[str, EmailEventConfig]] = None
    templates: Optional[Dict[str, EmailTemplateConfig]] = None
    cc_addresses: Optional[List[str]] = Field(default=None, max_length=5)
    bcc_addresses: Optional[List[str]] = Field(default=None, max_length=5)

    @field_validator("cc_addresses", "bcc_addresses")
    @classmethod
    def validate_copy_addresses(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        return None if value is None else _normalize_email_list(value)

    @model_validator(mode="after")
    def validate_event_names(self) -> "EmailConfigPatch":
        for block_name, block in (("events", self.events), ("templates", self.templates)):
            unknown = sorted(set((block or {}).keys()) - set(EMAIL_EVENTS))
            if unknown:
                raise ValueError(f"{block_name} desconhecidos: {', '.join(unknown)}")
        return self


async def _require_admin_or_portal(
    workspace_slug: str,
    x_admin_token: Optional[str] = Header(default=None),
    x_portal_token: Optional[str] = Header(default=None),
) -> None:
    if x_admin_token and _admin_token_is_valid(x_admin_token.strip()):
        return
    require_portal_session(workspace_slug, x_portal_token)


def _email_block(
    workspace_slug: str,
    workflow_type: str,
) -> tuple[Dict[str, Any], bool, str]:
    row = _read_raw_row(workspace_slug, workflow_type)
    extra = (row or {}).get("extra_settings") or {}
    email = extra.get("email") or {}
    editing = extra.get("editing") or {}
    default_policy = DEFAULT_EDIT_POLICIES.get(workflow_type, "disabled")
    edit_policy = str(editing.get("policy") or default_policy)
    if edit_policy not in EDIT_POLICIES:
        edit_policy = default_policy
    return (email if isinstance(email, dict) else {}), row is not None, edit_policy


def _resolve(workspace_slug: str, workflow_type: str) -> Dict[str, Any]:
    email, row_exists, edit_policy = _email_block(workspace_slug, workflow_type)
    raw_events = email.get("events") or {}
    raw_templates = email.get("templates") or {}

    events: Dict[str, Dict[str, Any]] = {}
    templates: Dict[str, Dict[str, str]] = {}
    for event in EMAIL_EVENTS:
        event_cfg = raw_events.get(event) or {}
        events[event] = {
            "enabled": bool(event_cfg.get("enabled", True)),
            "recipients": list(event_cfg.get("recipients") or [])[:5],
            "_origin": "db" if event in raw_events else "default",
        }
        template_cfg = raw_templates.get(event) or {}
        default_preview = _default_template_preview(
            workspace_slug,
            workflow_type,
            event,
            edit_policy,
        )
        templates[event] = {
            "subject": str(template_cfg.get("subject") or ""),
            "body": str(template_cfg.get("body") or ""),
            "default_subject": default_preview["subject"],
            "default_body": default_preview["body"],
            "_origin": "db" if event in raw_templates else "default",
        }

    return {
        "ok": True,
        "workspace_slug": workspace_slug,
        "workflow_type": workflow_type,
        "row_exists": row_exists,
        "events": events,
        "templates": templates,
        "cc_addresses": list(email.get("cc_addresses") or [])[:5],
        "bcc_addresses": list(email.get("bcc_addresses") or [])[:5],
        "placeholders": list(SUPPORTED_PLACEHOLDERS),
    }


@router.get("/{workspace_slug}/workflows/{workflow_type}/email-config")
async def get_email_config(
    workspace_slug: str,
    workflow_type: str,
    _: None = Depends(_require_admin_or_portal),
) -> Dict[str, Any]:
    return _resolve(workspace_slug.strip().lower(), workflow_type.strip().lower())


@router.patch("/{workspace_slug}/workflows/{workflow_type}/email-config")
async def patch_email_config(
    workspace_slug: str,
    workflow_type: str,
    body: EmailConfigPatch,
    _: None = Depends(_require_admin_or_portal),
) -> Dict[str, Any]:
    slug = workspace_slug.strip().lower()
    workflow = workflow_type.strip().lower()
    row = _read_raw_row(slug, workflow) or {}
    extra = copy.deepcopy(row.get("extra_settings") or {})
    email = copy.deepcopy(extra.get("email") or {})
    updated: List[str] = []

    if body.events is not None:
        events = email.setdefault("events", {})
        for event, config in body.events.items():
            events[event] = config.model_dump(mode="json")
            updated.append(f"events.{event}")

    if body.templates is not None:
        templates = email.setdefault("templates", {})
        for event, config in body.templates.items():
            templates[event] = config.model_dump(mode="json")
            updated.append(f"templates.{event}")

    if "cc_addresses" in body.model_fields_set:
        email["cc_addresses"] = body.cc_addresses or []
        updated.append("cc_addresses")
    if "bcc_addresses" in body.model_fields_set:
        email["bcc_addresses"] = body.bcc_addresses or []
        updated.append("bcc_addresses")

    if not updated:
        return _resolve(slug, workflow)

    extra["email"] = email
    try:
        supabase.table("workspace_workflow_settings").upsert(
            {
                "workspace_slug": slug,
                "workflow_type": workflow,
                "extra_settings": extra,
            },
            on_conflict="workspace_slug,workflow_type",
        ).execute()
    except Exception:
        logger.exception("email_config update failed workspace=%s workflow=%s", slug, workflow)
        raise HTTPException(status_code=500, detail="falha ao persistir configuração de e-mail")

    result = _resolve(slug, workflow)
    result["updated"] = sorted(updated)
    return result
