from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from app.core.config import settings

logger = logging.getLogger("sunbeat.email")


def _post_resend(*, to_email: str, subject: str, html: str) -> Dict[str, Any]:
    if not settings.RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not configured")

    if not settings.RESEND_FROM_EMAIL:
        raise RuntimeError("RESEND_FROM_EMAIL is not configured")

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": settings.RESEND_FROM_EMAIL,
            "to": [to_email],
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

    subject = "Editar submissão"

    greeting = f"Olá, {recipient_name}!" if recipient_name else "Olá!"
    project_line = (
        f"Sua submissão para <strong>{project_title}</strong> foi recebida."
        if project_title
        else "Sua submissão foi recebida."
    )

    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #111;">
      <p>{greeting}</p>
      <p>{project_line}</p>
      <p>Você pode editar sua submissão pelo link abaixo:</p>
      <p>
        <a href="{edit_url}" style="color: #2563eb; text-decoration: none;">
          {edit_url}
        </a>
      </p>
      <p>Se você não solicitou alterações, pode ignorar este email.</p>
    </div>
    """

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

    greeting = f"Olá, {recipient_name}!" if recipient_name else "Olá!"
    project_line = (
        f"Seu rascunho para <strong>{project_title}</strong> foi salvo."
        if project_title
        else "Seu rascunho foi salvo."
    )

    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #111;">
      <p>{greeting}</p>
      <p>{project_line}</p>
      <p>Você pode continuar o preenchimento pelo link abaixo:</p>
      <p>
        <a href="{draft_url}" style="color: #2563eb; text-decoration: none;">
          {draft_url}
        </a>
      </p>
      <p>Esse link leva você de volta ao formulário com seu rascunho carregado.</p>
    </div>
    """

    return _post_resend(
        to_email=to_email,
        subject=subject,
        html=html,
    )