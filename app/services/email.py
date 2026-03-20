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


def _post_resend(
    *,
    to_email: str | Iterable[str],
    subject: str,
    html: str,
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

    if response.status_code >= 400:
        logger.error("Resend error: %s", response.text)
        raise RuntimeError(f"Failed to send email: {response.text}")

    return response.json()


def build_edit_url(edit_token: str, workspace_slug: str = "atabaque") -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/intake/{workspace_slug}?edit_token={edit_token}"


def build_draft_resume_url(draft_token: str, workspace_slug: str = "atabaque") -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/intake/{workspace_slug}?draft={draft_token}"


def _wrap_email_html(content: str) -> str:
    return f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #111827;">
      {content}
    </div>
    """


def send_edit_link_email(
    *,
    to_email: str,
    edit_token: str,
    project_title: Optional[str] = None,
    recipient_name: Optional[str] = None,
    workspace_slug: str = "atabaque",
) -> Dict[str, Any]:
    edit_url = build_edit_url(
        edit_token=edit_token,
        workspace_slug=workspace_slug,
    )

    subject = "Editar submissao"

    greeting = (
        f"Ola, {escape(recipient_name)}!"
        if recipient_name
        else "Ola!"
    )
    project_line = (
        f"Sua submissao para <strong>{escape(project_title)}</strong> foi recebida."
        if project_title
        else "Sua submissao foi recebida."
    )

    html = _wrap_email_html(
        f"""
        <p>{greeting}</p>
        <p>{project_line}</p>
        <p>Voce pode editar sua submissao pelo link abaixo:</p>
        <p>
          <a href="{edit_url}" style="color: #2563eb; text-decoration: none;">
            {edit_url}
          </a>
        </p>
        <p>Se voce nao solicitou alteracoes, pode ignorar este email.</p>
        """
    )

    return _post_resend(
        to_email=to_email,
        subject=subject,
        html=html,
    )


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
          <a href="{draft_url}" style="color: #2563eb; text-decoration: none;">
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

    return _post_resend(
        to_email=recipients,
        subject=subject,
        html=html,
    )
