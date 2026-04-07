from __future__ import annotations

import logging
from html import escape
from typing import Any, Dict, Iterable, List, Optional

import requests

from app.core.config import settings

logger = logging.getLogger("sunbeat.email")


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
        },
        json={
            "from": settings.RESEND_FROM_EMAIL,
            "to": recipients,
            "subject": subject,
            "html": html,
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


def build_draft_resume_url(draft_token: str, workspace_slug: str = "atabaque") -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/intake/{workspace_slug}?draft={draft_token}"


def _wrap_email_html(content: str) -> str:
    return f"""
    <div style="background-color: #f9fafb; padding: 32px 16px; font-family: Arial, sans-serif;">
      <div style="max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e5e7eb; padding: 32px; line-height: 1.7; color: #111827;">
        {content}
        <hr style="margin: 32px 0; border: none; border-top: 1px solid #e5e7eb;">
        <p style="margin: 0; font-size: 12px; color: #6b7280; text-align: center;">Sunbeat &mdash; Infraestrutura para Lançamentos Musicais</p>
      </div>
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
) -> Dict[str, Any]:
    edit_url = build_edit_url(
        edit_token=edit_token,
        workspace_slug=workspace_slug,
    )

    safe_project_title = (project_title or "").strip() or "Projeto sem titulo"
    safe_release_date = (release_date or "").strip() or "data nao informada"
    safe_primary_artist = (primary_artist or "").strip() or "artista nao informado"

    subject = (
        f"Resumo do lan\u00e7amento - {safe_project_title} - "
        f"{safe_release_date} + {safe_primary_artist}"
    )

    greeting = (
        f"Ola, {escape(recipient_name)}!"
        if recipient_name
        else "Ola!"
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
          <a href="{edit_url}" style="color: #1d4ed8; text-decoration: underline; word-break: break-all;">
            {edit_url}
          </a>
        </p>
        <p>Se voce nao reconhece este envio, pode ignorar este email.</p>
        """
    )

    return _post_resend(
        to_email=to_email,
        subject=subject,
        html=html,
        edit_url=edit_url,
    ) | {"to_email": to_email}


def send_draft_link_email(
    *,
    to_email: str,
    draft_token: str,
    project_title: Optional[str] = None,
    recipient_name: Optional[str] = None,
    workspace_slug: str = "atabaque",
) -> Dict[str, Any]:
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
          <a href="{draft_url}" style="color: #1d4ed8; text-decoration: underline; word-break: break-all;">
            {draft_url}
          </a>
        </p>
        <p>Esse link leva voce de volta ao formulario com seu rascunho carregado.</p>
        """
    )

    return _post_resend(
        to_email=to_email,
        subject=subject,
        html=html,
    )


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

    track_items = [
        f"<li>{escape(track_title)}</li>"
        for track_title in track_titles
        if str(track_title).strip()
    ]
    tracks_html = "".join(track_items) if track_items else "<li>Nenhuma faixa informada</li>"

    subject = f"Nova submissao recebida - {project_title or workspace_name}"

    html = _wrap_email_html(
        f"""
        <p>O intake da <strong>{safe_workspace_name}</strong> recebeu uma nova submissao.</p>

        <table style="border-collapse: collapse; width: 100%; margin: 24px 0; background: #f9fafb; border-radius: 8px; overflow: hidden;">
          <tbody>
            <tr><td style="padding: 10px 14px; color: #374151; font-weight: 600; white-space: nowrap; vertical-align: top; border-bottom: 1px solid #e5e7eb;">Projeto</td><td style="padding: 10px 14px; color: #111827; border-bottom: 1px solid #e5e7eb;"><strong>{safe_project_title}</strong></td></tr>
            <tr><td style="padding: 10px 14px; color: #374151; font-weight: 600; white-space: nowrap; vertical-align: top; border-bottom: 1px solid #e5e7eb;">Responsavel</td><td style="padding: 10px 14px; color: #111827; border-bottom: 1px solid #e5e7eb;">{safe_submitter_name}</td></tr>
            <tr><td style="padding: 10px 14px; color: #374151; font-weight: 600; white-space: nowrap; vertical-align: top; border-bottom: 1px solid #e5e7eb;">E-mail</td><td style="padding: 10px 14px; color: #111827; border-bottom: 1px solid #e5e7eb;">{safe_submitter_email}</td></tr>
            <tr><td style="padding: 10px 14px; color: #374151; font-weight: 600; white-space: nowrap; vertical-align: top; border-bottom: 1px solid #e5e7eb;">Tipo de lancamento</td><td style="padding: 10px 14px; color: #111827; border-bottom: 1px solid #e5e7eb;">{safe_release_type}</td></tr>
            <tr><td style="padding: 10px 14px; color: #374151; font-weight: 600; white-space: nowrap; vertical-align: top; border-bottom: 1px solid #e5e7eb;">Data prevista</td><td style="padding: 10px 14px; color: #111827; border-bottom: 1px solid #e5e7eb;">{safe_release_date}</td></tr>
            <tr><td style="padding: 10px 14px; color: #374151; font-weight: 600; white-space: nowrap; vertical-align: top; border-bottom: 1px solid #e5e7eb;">Genero</td><td style="padding: 10px 14px; color: #111827; border-bottom: 1px solid #e5e7eb;">{safe_genre}</td></tr>
            <tr><td style="padding: 10px 14px; color: #374151; font-weight: 600; white-space: nowrap; vertical-align: top;">Faixa foco</td><td style="padding: 10px 14px; color: #111827;">{safe_focus_track_name}</td></tr>
          </tbody>
        </table>

        <p style="margin-bottom: 8px;"><strong>Faixas enviadas</strong></p>
        <ul style="margin-top: 0; padding-left: 18px; color: #111827;">
          {tracks_html}
        </ul>

        <p style="margin-top: 24px;">Para revisar ou ajustar a submissao, use o link abaixo:</p>
        <p>
          <a href="{edit_url}" style="color: #1d4ed8; text-decoration: underline; word-break: break-all;">
            {edit_url}
          </a>
        </p>
        """
    )

    return _post_resend(
        to_email=recipients,
        subject=subject,
        html=html,
    )
