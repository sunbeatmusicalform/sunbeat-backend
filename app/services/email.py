from __future__ import annotations

import logging
import re
from html import escape
from typing import Any, Dict, Iterable, List, Optional

import requests

from app.core.config import settings
from app.services.workspace_config import (
    get_email_extra_config,
    get_email_event_config,
    get_email_template_config,
    is_email_event_enabled,
)

logger = logging.getLogger("sunbeat.email")

_TEMPLATE_TOKEN_RE = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")


def _normalize_recipients(value: str | Iterable[str]) -> List[str]:
    if isinstance(value, str):
        candidates = [value]
    else:
        candidates = list(value)

    unique: list[str] = []
    seen: set[str] = set()

    for item in candidates:
        normalized = str(item).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)

    return unique


def _merge_recipients(
    *groups: Optional[Iterable[str]],
    exclude: Optional[Iterable[str]] = None,
) -> List[str]:
    excluded = set(_normalize_recipients(exclude or []))
    merged: List[str] = []
    for group in groups:
        merged.extend(list(group or []))
    return [address for address in _normalize_recipients(merged) if address not in excluded]


def render_email_template(template: str, context: Dict[str, Any]) -> str:
    """Renderiza placeholders conhecidos escapando todo valor vindo do formulário."""
    safe_context = {
        str(key): escape(str(value if value is not None else ""), quote=True)
        for key, value in context.items()
    }
    return _TEMPLATE_TOKEN_RE.sub(
        lambda match: safe_context.get(match.group(1), match.group(0)),
        str(template or ""),
    )


def _customize_email(
    *,
    workspace_slug: str,
    workflow_type: str,
    event: str,
    default_subject: str,
    default_html: str,
    context: Dict[str, Any],
    variant: Optional[str] = None,
) -> tuple[str, str]:
    template = get_email_template_config(
        workspace_slug,
        workflow_type,
        event,
        variant=variant,
    )
    subject_template = template.get("subject") or ""
    body_template = template.get("body") or ""
    subject = (
        render_email_template(subject_template, context).replace("\r", " ").replace("\n", " ")
        if subject_template
        else default_subject
    )
    html = render_email_template(body_template, context) if body_template else default_html
    return subject, html


def _extract_provider_message_id(
    provider_response: Any,
    headers: requests.structures.CaseInsensitiveDict[str] | Dict[str, Any],
) -> Optional[str]:
    if isinstance(provider_response, dict):
        for key in ("id", "message_id", "messageId"):
            value = provider_response.get(key)
            if value:
                return str(value)

        data = provider_response.get("data")
        if isinstance(data, dict):
            for key in ("id", "message_id", "messageId"):
                value = data.get(key)
                if value:
                    return str(value)

    for header_name in ("x-message-id", "x-email-id"):
        header_value = headers.get(header_name)
        if header_value:
            return str(header_value)

    return None


def _post_resend(
    *,
    to_email: str | Iterable[str],
    subject: str,
    html: str,
    edit_url: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    if not settings.RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not configured")

    if not settings.RESEND_FROM_EMAIL:
        raise RuntimeError("RESEND_FROM_EMAIL is not configured")

    recipients = _normalize_recipients(to_email)
    if not recipients:
        raise RuntimeError("At least one recipient is required")

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
            **({"Idempotency-Key": idempotency_key} if idempotency_key else {}),
        },
        json={
            "from": settings.RESEND_FROM_EMAIL,
            "to": recipients,
            "subject": subject,
            "html": html,
            **( {"cc": cc} if cc else {} ),
            **( {"bcc": bcc} if bcc else {} ),
        },
        timeout=30,
    )

    response_text = response.text

    try:
        provider_response = response.json() if response_text else {}
    except ValueError:
        provider_response = {"raw_text": response_text}

    if response.status_code >= 400:
        logger.error(
            "Resend error status=%s subject=%s response=%s",
            response.status_code,
            subject,
            provider_response,
        )
        raise RuntimeError(f"Failed to send email: {response_text}")

    provider_message_id = _extract_provider_message_id(
        provider_response,
        response.headers,
    )

    logger.info(
        "Resend accepted email subject=%s recipients=%s status=%s message_id=%s response=%s",
        subject,
        recipients,
        response.status_code,
        provider_message_id,
        provider_response,
    )

    return {
        "provider": "resend",
        "provider_status_code": response.status_code,
        "provider_response": provider_response,
        "provider_message_id": provider_message_id,
        "subject": subject,
        "edit_url": edit_url,
    }


def build_edit_url(edit_token: str, workspace_slug: str = "atabaque") -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/intake/{workspace_slug}?edit_token={edit_token}"


def build_workflow_edit_url(
    *,
    edit_token: str,
    workspace_slug: str = "atabaque",
    workflow_type: Optional[str] = None,
) -> str:
    if not workflow_type or workflow_type == "release_intake":
        return build_edit_url(edit_token=edit_token, workspace_slug=workspace_slug)

    try:
        from app.modules.workflow_registry import build_frontend_workflow_path

        base = settings.FRONTEND_BASE_URL.rstrip("/")
        path = build_frontend_workflow_path(
            workspace_slug=workspace_slug,
            workflow_type=workflow_type,
        )
        return f"{base}{path}?edit_token={edit_token}"
    except Exception:
        logger.warning(
            "Could not build workflow edit URL workspace_slug=%s workflow_type=%s",
            workspace_slug,
            workflow_type,
        )
        return build_edit_url(edit_token=edit_token, workspace_slug=workspace_slug)


def build_draft_resume_url(draft_token: str, workspace_slug: str = "atabaque") -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/intake/{workspace_slug}?draft={draft_token}"


def _wrap_email_html(content: str) -> str:
    return f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #111827;">
      {content}
    </div>
    """


def _build_days_until_release_copy(days_until_release: Optional[int]) -> str:
    if days_until_release is None:
        return "Nao foi possivel calcular quantos dias faltam para o lancamento."

    if days_until_release > 1:
        return f"Faltam <strong>{days_until_release}</strong> dias para o lancamento."

    if days_until_release == 1:
        return "Falta <strong>1</strong> dia para o lancamento."

    if days_until_release == 0:
        return "O lancamento esta previsto para <strong>hoje</strong>."

    return (
        f"A data informada indica que o lancamento ocorreu ha "
        f"<strong>{abs(days_until_release)}</strong> dias."
    )


def send_edit_link_email(
    *,
    to_email: str,
    edit_token: str,
    project_title: Optional[str] = None,
    release_date: Optional[str] = None,
    primary_artist: Optional[str] = None,
    days_until_release: Optional[int] = None,
    recipient_name: Optional[str] = None,
    workspace_slug: str = "atabaque",
    workflow_type: Optional[str] = None,
    event: str = "on_submit",
    variant: Optional[str] = None,
) -> Dict[str, Any]:
    # [MT-OBS] PR-01 — log de workspace para observabilidade multi-tenant
    logger.info(
        "send_edit_link_email workspace_slug=%s to_email=%s project_title=%s workflow_type=%s",
        workspace_slug,
        to_email,
        project_title,
        workflow_type,
    )

    # Resolver destinatarios internos via extra_settings.email.events (v2) com fallback v1
    _wf = workflow_type or "release_intake"
    if not is_email_event_enabled(
        workspace_slug,
        _wf,
        event,
        variant=variant,
        legacy_default=True,
    ):
        return {
            "status": "disabled",
            "skipped": True,
            "subject": None,
            "edit_url": None,
            "to_email": to_email,
        }
    _email_extra = get_email_extra_config(workspace_slug, _wf)
    _ev_cfg = get_email_event_config(workspace_slug, _wf, event, variant=variant)
    # v2: per-event recipients; fallback v1: cc_addresses global
    _cc = _merge_recipients(
        _ev_cfg.get("recipients") or [],
        _email_extra.get("cc_addresses") or [],
        exclude=[to_email],
    ) or None
    _bcc = _merge_recipients(
        _email_extra.get("bcc_addresses") or [],
        exclude=[to_email, *(_cc or [])],
    ) or None

    edit_url = build_edit_url(
        edit_token=edit_token,
        workspace_slug=workspace_slug,
    )
    if workflow_type and workflow_type != "release_intake":
        edit_url = build_workflow_edit_url(
            edit_token=edit_token,
            workspace_slug=workspace_slug,
            workflow_type=workflow_type,
        )

    safe_project_title = (project_title or "").strip() or "Projeto sem titulo"
    greeting = (
        f"Ola, {escape(recipient_name)}!"
        if recipient_name
        else "Ola!"
    )
    template_context = {
        "submitter_name": recipient_name or "",
        "submitter_email": to_email,
        "project_title": safe_project_title,
        "release_date": release_date or "",
        "release_type": "",
        "genre": "",
        "primary_artist": primary_artist or "",
        "draft_link": "",
        "edit_link": edit_url,
        "workspace_name": workspace_slug.replace("-", " ").title(),
        "current_step": "",
        "tracks_count": "",
        "focus_track": "",
        "track_titles": "",
    }

    # -- rights_clearance --
    if workflow_type == "rights_clearance":
        subject = f"Solicitacao de clearance recebida - {safe_project_title}"
        html = _wrap_email_html(
            f"""
        <p>{greeting}</p>
        <p>Obrigada pelo envio.</p>
        <p>Recebemos a sua solicitacao de clearance para
        <strong>{escape(safe_project_title)}</strong>.</p>
        <p>Nossa equipe vai analisar as informacoes enviadas e entrar em contato
        se precisar de algo adicional.</p>
        <p>
          Se precisar revisar ou atualizar as informacoes enviadas, use o link abaixo:
        </p>
        <p>
          <a href="{edit_url}" style="color: #2563eb; text-decoration: none;">
            {edit_url}
          </a>
        </p>
        <p>Se voce nao reconhece este envio, pode ignorar este email.</p>
            """
        )
        subject, html = _customize_email(
            workspace_slug=workspace_slug,
            workflow_type=_wf,
            event=event,
            default_subject=subject,
            default_html=html,
            context=template_context,
            variant=variant,
        )
        return _post_resend(
            to_email=to_email,
            subject=subject,
            html=html,
            edit_url=edit_url,
            cc=_cc,
            bcc=_bcc,
        ) | {"to_email": to_email}

    # -- company_registry --
    if workflow_type == "company_registry":
        subject = f"Cadastro recebido - {safe_project_title}"
        html = _wrap_email_html(
            f"""
        <p>{greeting}</p>
        <p>Obrigada pelo envio.</p>
        <p>Recebemos o cadastro de <strong>{escape(safe_project_title)}</strong>.</p>
        <p>Nossa equipe vai revisar as informacoes e entrar em contato em breve.</p>
        <p>
          Se precisar revisar ou atualizar os dados enviados, use o link abaixo:
        </p>
        <p>
          <a href="{edit_url}" style="color: #2563eb; text-decoration: none;">
            {edit_url}
          </a>
        </p>
        <p>Se voce nao reconhece este envio, pode ignorar este email.</p>
            """
        )
        subject, html = _customize_email(
            workspace_slug=workspace_slug,
            workflow_type=_wf,
            event=event,
            default_subject=subject,
            default_html=html,
            context=template_context,
            variant=variant,
        )
        return _post_resend(
            to_email=to_email,
            subject=subject,
            html=html,
            edit_url=edit_url,
            cc=_cc,
            bcc=_bcc,
        ) | {"to_email": to_email}

    # -- people_registry --
    if workflow_type == "people_registry":
        subject = f"Cadastro recebido - {safe_project_title}"
        html = _wrap_email_html(
            f"""
        <p>{greeting}</p>
        <p>Obrigada pelo envio.</p>
        <p>Recebemos o cadastro de <strong>{escape(safe_project_title)}</strong>.</p>
        <p>Nossa equipe vai revisar as informacoes e entrar em contato se precisar de algo adicional.</p>
        <p>Se precisar revisar ou atualizar os dados enviados, use o link abaixo:</p>
        <p><a href="{edit_url}" style="color: #2563eb; text-decoration: none;">{edit_url}</a></p>
        <p>Se voce nao reconhece este envio, pode ignorar este email.</p>
            """
        )
        subject, html = _customize_email(
            workspace_slug=workspace_slug,
            workflow_type=_wf,
            event=event,
            default_subject=subject,
            default_html=html,
            context=template_context,
            variant=variant,
        )
        return _post_resend(
            to_email=to_email,
            subject=subject,
            html=html,
            edit_url=edit_url,
            cc=_cc,
            bcc=_bcc,
        ) | {"to_email": to_email}

    # -- release_intake (default) — comportamento original preservado --
    safe_release_date = (release_date or "").strip() or "data nao informada"
    safe_primary_artist = (primary_artist or "").strip() or "artista nao informado"

    subject = (
        f"Resumo do lan\u00e7amento - {safe_project_title} - "
        f"{safe_release_date} + {safe_primary_artist}"
    )

    project_line = (
        f"Recebemos o envio do lancamento "
        f"<strong>{escape(safe_project_title)}</strong>."
    )
    release_date_line = (
        f"A data informada para o lancamento e "
        f"<strong>{escape(safe_release_date)}</strong>."
    )
    days_until_release_line = _build_days_until_release_copy(days_until_release)

    html = _wrap_email_html(
        f"""
        <p>{greeting}</p>
        <p>Obrigada pelo envio.</p>
        <p>{project_line}</p>
        <p>{release_date_line}</p>
        <p>{days_until_release_line}</p>
        <p>
          A partir do link abaixo, voce pode editar a submissao sempre que precisar
          revisar ou atualizar as informacoes enviadas:
        </p>
        <p>
          <a href="{edit_url}" style="color: #2563eb; text-decoration: none;">
            {edit_url}
          </a>
        </p>
        <p>Se voce nao reconhece este envio, pode ignorar este email.</p>
        """
    )

    subject, html = _customize_email(
        workspace_slug=workspace_slug,
        workflow_type=_wf,
        event=event,
        default_subject=subject,
        default_html=html,
        context=template_context,
        variant=variant,
    )

    return _post_resend(
        to_email=to_email,
        subject=subject,
        html=html,
        edit_url=edit_url,
        cc=_cc,
        bcc=_bcc,
    ) | {"to_email": to_email}


def send_draft_link_email(
    *,
    to_email: str,
    draft_token: str,
    project_title: Optional[str] = None,
    recipient_name: Optional[str] = None,
    workspace_slug: str = "atabaque",
    workflow_type: Optional[str] = None,
) -> Dict[str, Any]:
    # [MT-OBS] PR-01 — log de workspace para observabilidade multi-tenant
    logger.info(
        "send_draft_link_email workspace_slug=%s to_email=%s draft_token=%s",
        workspace_slug,
        to_email,
        draft_token,
    )

    draft_url = build_draft_resume_url(
        draft_token=draft_token,
        workspace_slug=workspace_slug,
    )

    subject = "Continue o preenchimento do seu rascunho"

    greeting = (
        f"Ola, {escape(recipient_name)}!"
        if recipient_name
        else "Ola!"
    )
    project_line = (
        f"Seu rascunho para <strong>{escape(project_title)}</strong> foi salvo."
        if project_title
        else "Seu rascunho foi salvo."
    )

    html = _wrap_email_html(
        f"""
        <p>{greeting}</p>
        <p>{project_line}</p>
        <p>Voce pode continuar o preenchimento pelo link abaixo:</p>
        <p>
          <a href="{draft_url}" style="color: #2563eb; text-decoration: none;">
            {draft_url}
          </a>
        </p>
        <p>Esse link leva voce de volta ao formulario com seu rascunho carregado.</p>
        """
    )

    _draft_ev = get_email_event_config(
        workspace_slug, workflow_type or "release_intake", "on_draft"
    )
    _draft_extra = get_email_extra_config(workspace_slug, workflow_type or "release_intake")
    _draft_cc = _merge_recipients(
        _draft_ev.get("recipients") or [],
        _draft_extra.get("cc_addresses") or [],
        exclude=[to_email],
    ) or None
    _draft_bcc = _merge_recipients(
        _draft_extra.get("bcc_addresses") or [],
        exclude=[to_email, *(_draft_cc or [])],
    ) or None
    subject, html = _customize_email(
        workspace_slug=workspace_slug,
        workflow_type=workflow_type or "release_intake",
        event="on_draft",
        default_subject=subject,
        default_html=html,
        context={
            "submitter_name": recipient_name or "",
            "submitter_email": to_email,
            "project_title": project_title or "",
            "release_date": "",
            "release_type": "",
            "genre": "",
            "primary_artist": "",
            "draft_link": draft_url,
            "edit_link": "",
            "workspace_name": workspace_slug.replace("-", " ").title(),
            "current_step": "",
            "tracks_count": "",
            "focus_track": "",
            "track_titles": "",
        },
    )
    return _post_resend(
        to_email=to_email,
        subject=subject,
        html=html,
        cc=_draft_cc,
        bcc=_draft_bcc,
    )


def send_people_registry_invite_email(
    *,
    to_email: str,
    invite_url: str,
    recipient_name: Optional[str] = None,
    project_title: Optional[str] = None,
    track_title: Optional[str] = None,
    role: Optional[str] = None,
    remuneration: Optional[Any] = None,
    expires_at: Optional[str] = None,
    message: Optional[str] = None,
    workspace_slug: str = "atabaque",
) -> Dict[str, Any]:
    logger.info(
        "send_people_registry_invite_email workspace_slug=%s to_email=%s project_title=%s track_title=%s",
        workspace_slug,
        to_email,
        project_title,
        track_title,
    )

    safe_project = escape(str(project_title or "projeto informado").strip())
    safe_track = escape(str(track_title or "").strip())
    safe_role = escape(str(role or "").strip())
    safe_remuneration = escape(str(remuneration or "").strip())
    safe_message = escape(str(message or "").strip())
    safe_invite_url = escape(invite_url)
    greeting = (
        f"Ola, {escape(recipient_name)}!"
        if recipient_name
        else "Ola!"
    )

    details_rows = [
        ("Projeto", safe_project),
        ("Faixa", safe_track),
        ("Funcao", safe_role),
        ("Remuneracao", safe_remuneration),
    ]
    rows_html = "".join(
        f"""
        <tr>
          <td style="padding: 8px 0; color: #6b7280;">{label}</td>
          <td style="padding: 8px 0;"><strong>{value}</strong></td>
        </tr>
        """
        for label, value in details_rows
        if value
    )
    expires_copy = (
        f"<p>Esse link fica disponivel ate <strong>{escape(str(expires_at))}</strong>.</p>"
        if expires_at
        else ""
    )
    message_copy = f"<p>{safe_message}</p>" if safe_message else ""

    subject = f"Cadastro para projeto - {project_title or 'Sunbeat'}"
    html = _wrap_email_html(
        f"""
        <p>{greeting}</p>
        <p>Voce recebeu um link para informar seus dados de cadastro relacionados a um projeto.</p>
        {message_copy}
        <table style="border-collapse: collapse; width: 100%; margin: 20px 0;">
          <tbody>{rows_html}</tbody>
        </table>
        <p>Use o link abaixo para preencher ou confirmar as informacoes:</p>
        <p>
          <a href="{safe_invite_url}" style="color: #2563eb; text-decoration: none;">
            {safe_invite_url}
          </a>
        </p>
        {expires_copy}
        <p>Se voce nao reconhece este pedido, pode ignorar este email.</p>
        """
    )

    return _post_resend(
        to_email=to_email,
        subject=subject,
        html=html,
        edit_url=invite_url,
    ) | {"to_email": to_email, "invite_url": invite_url}


def send_first_stage_completion_email(
    *,
    to_emails: Iterable[str],
    workspace_name: str,
    submitter_name: Optional[str],
    submitter_email: str,
    project_title: Optional[str],
    draft_token: str,
    current_step: Optional[str],
    workspace_slug: str = "atabaque",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    recipients = _normalize_recipients(to_emails)
    if not recipients:
        raise RuntimeError("No notification recipients configured")

    draft_url = build_draft_resume_url(
        draft_token=draft_token,
        workspace_slug=workspace_slug,
    )

    safe_workspace_name = escape(workspace_name or "Sunbeat")
    safe_project_title = escape(project_title or "Sem titulo")
    safe_submitter_name = escape(submitter_name or "Responsavel nao informado")
    safe_submitter_email = escape(submitter_email or "Nao informado")
    safe_current_step = escape(current_step or "nao informado")

    subject = f"Primeira etapa concluida - {project_title or workspace_name}"

    html = _wrap_email_html(
        f"""
        <p>O intake da <strong>{safe_workspace_name}</strong> recebeu a conclusao da primeira etapa do formulario.</p>

        <table style="border-collapse: collapse; width: 100%; margin: 24px 0;">
          <tbody>
            <tr><td style="padding: 8px 0; color: #6b7280;">Projeto</td><td style="padding: 8px 0;"><strong>{safe_project_title}</strong></td></tr>
            <tr><td style="padding: 8px 0; color: #6b7280;">Responsavel</td><td style="padding: 8px 0;">{safe_submitter_name}</td></tr>
            <tr><td style="padding: 8px 0; color: #6b7280;">E-mail</td><td style="padding: 8px 0;">{safe_submitter_email}</td></tr>
            <tr><td style="padding: 8px 0; color: #6b7280;">Etapa atual</td><td style="padding: 8px 0;">{safe_current_step}</td></tr>
          </tbody>
        </table>

        <p>Para continuar o atendimento operacional ou retomar o rascunho, use o link abaixo:</p>
        <p>
          <a href="{draft_url}" style="color: #2563eb; text-decoration: none;">
            {draft_url}
          </a>
        </p>
        """
    )

    subject, html = _customize_email(
        workspace_slug=workspace_slug,
        workflow_type="release_intake",
        event="on_first_stage",
        default_subject=subject,
        default_html=html,
        context={
            "submitter_name": submitter_name or "",
            "submitter_email": submitter_email,
            "project_title": project_title or "",
            "release_date": "",
            "release_type": "",
            "genre": "",
            "primary_artist": "",
            "draft_link": draft_url,
            "edit_link": "",
            "workspace_name": workspace_name,
            "current_step": current_step or "",
            "tracks_count": "",
            "focus_track": "",
            "track_titles": "",
        },
    )
    email_extra = get_email_extra_config(workspace_slug, "release_intake")
    cc = _merge_recipients(email_extra.get("cc_addresses") or [], exclude=recipients) or None
    bcc = _merge_recipients(
        email_extra.get("bcc_addresses") or [],
        exclude=[*recipients, *(cc or [])],
    ) or None

    result = _post_resend(
        to_email=recipients,
        subject=subject,
        html=html,
        cc=cc,
        bcc=bcc,
        idempotency_key=idempotency_key,
    )
    status = (
        "sent"
        if result.get("provider_message_id")
        else "sent_without_message_id"
    )
    return result | {"draft_url": draft_url, "status": status}


def send_submission_summary_email(
    *,
    to_emails: Iterable[str],
    workspace_name: str,
    submitter_name: Optional[str],
    submitter_email: str,
    project_title: Optional[str],
    release_type: Optional[str],
    release_date: Optional[str],
    genre: Optional[str],
    focus_track_name: Optional[str],
    track_titles: Iterable[str],
    edit_url: str,
    workspace_slug: str = "atabaque",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    recipients = _normalize_recipients(to_emails)
    if not recipients:
        raise RuntimeError("No notification recipients configured")

    safe_workspace_name = escape(workspace_name or "Sunbeat")
    safe_project_title = escape(project_title or "Sem titulo")
    safe_submitter_name = escape(submitter_name or "Responsavel nao informado")
    safe_submitter_email = escape(submitter_email)
    safe_release_type = escape(release_type or "Nao informado")
    safe_release_date = escape(release_date or "Nao informada")
    safe_genre = escape(genre or "Nao informado")
    safe_focus_track_name = escape(focus_track_name or "Nao definida")

    track_title_list = [
        str(track_title).strip()
        for track_title in track_titles
        if str(track_title).strip()
    ]
    track_items = [
        f"<li>{escape(track_title)}</li>"
        for track_title in track_title_list
    ]
    tracks_html = "".join(track_items) if track_items else "<li>Nenhuma faixa informada</li>"

    subject = f"Nova submissao recebida - {project_title or workspace_name}"

    html = _wrap_email_html(
        f"""
        <p>O intake da <strong>{safe_workspace_name}</strong> recebeu uma nova submissao.</p>

        <table style="border-collapse: collapse; width: 100%; margin: 24px 0;">
          <tbody>
            <tr><td style="padding: 8px 0; color: #6b7280;">Projeto</td><td style="padding: 8px 0;"><strong>{safe_project_title}</strong></td></tr>
            <tr><td style="padding: 8px 0; color: #6b7280;">Responsavel</td><td style="padding: 8px 0;">{safe_submitter_name}</td></tr>
            <tr><td style="padding: 8px 0; color: #6b7280;">E-mail</td><td style="padding: 8px 0;">{safe_submitter_email}</td></tr>
            <tr><td style="padding: 8px 0; color: #6b7280;">Tipo de lancamento</td><td style="padding: 8px 0;">{safe_release_type}</td></tr>
            <tr><td style="padding: 8px 0; color: #6b7280;">Data prevista</td><td style="padding: 8px 0;">{safe_release_date}</td></tr>
            <tr><td style="padding: 8px 0; color: #6b7280;">Genero</td><td style="padding: 8px 0;">{safe_genre}</td></tr>
            <tr><td style="padding: 8px 0; color: #6b7280;">Faixa foco</td><td style="padding: 8px 0;">{safe_focus_track_name}</td></tr>
          </tbody>
        </table>

        <p style="margin-bottom: 8px;"><strong>Faixas enviadas</strong></p>
        <ul style="margin-top: 0; padding-left: 18px;">
          {tracks_html}
        </ul>

        <p style="margin-top: 24px;">Para revisar ou ajustar a submissao, use o link abaixo:</p>
        <p>
          <a href="{edit_url}" style="color: #2563eb; text-decoration: none;">
            {edit_url}
          </a>
        </p>
        """
    )

    subject, html = _customize_email(
        workspace_slug=workspace_slug,
        workflow_type="release_intake",
        event="on_summary",
        default_subject=subject,
        default_html=html,
        context={
            "submitter_name": submitter_name or "",
            "submitter_email": submitter_email,
            "project_title": project_title or "",
            "release_date": release_date or "",
            "release_type": release_type or "",
            "genre": genre or "",
            "primary_artist": "",
            "draft_link": "",
            "edit_link": edit_url,
            "workspace_name": workspace_name,
            "current_step": "",
            "tracks_count": len(track_title_list),
            "focus_track": focus_track_name or "",
            "track_titles": ", ".join(track_title_list),
        },
    )
    email_extra = get_email_extra_config(workspace_slug, "release_intake")
    cc = _merge_recipients(email_extra.get("cc_addresses") or [], exclude=recipients) or None
    bcc = _merge_recipients(
        email_extra.get("bcc_addresses") or [],
        exclude=[*recipients, *(cc or [])],
    ) or None

    return _post_resend(
        to_email=recipients,
        subject=subject,
        html=html,
        cc=cc,
        bcc=bcc,
        idempotency_key=idempotency_key,
    )
