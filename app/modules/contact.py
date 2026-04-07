from __future__ import annotations

import logging
from html import escape
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr

from app.core.config import settings
from app.services.email import _post_resend, _wrap_email_html  # noqa: WPS450

logger = logging.getLogger("sunbeat.contact")

router = APIRouter()

CONTACT_NOTIFICATION_EMAIL = "contatofelipefonsek@gmail.com"

ROLE_LABELS: dict[str, str] = {
    "artist": "Artista",
    "label": "Label / Distribuidora",
    "manager": "Manager",
    "company": "Empresa / Agência",
    "other": "Outro",
}


class ContactPayload(BaseModel):
    name: str
    email: EmailStr
    company: Optional[str] = None
    role: Optional[str] = None
    message: str


@router.post("/contact")
def submit_contact(payload: ContactPayload) -> dict:
    safe_name = escape(payload.name.strip())
    safe_email = escape(str(payload.email).strip())
    safe_company = escape(payload.company.strip()) if payload.company else "—"
    safe_role = escape(ROLE_LABELS.get(payload.role or "", payload.role or "Não informado"))
    safe_message = escape(payload.message.strip()).replace("\n", "<br>")

    html = _wrap_email_html(
        f"""
        <p>Nova solicitação de acesso recebida via sunbeat.pro/contact.</p>

        <table style="border-collapse: collapse; width: 100%; margin: 24px 0;">
          <tbody>
            <tr>
              <td style="padding: 8px 12px 8px 0; color: #374151; font-weight: 600; white-space: nowrap; vertical-align: top;">Nome</td>
              <td style="padding: 8px 0;"><strong>{safe_name}</strong></td>
            </tr>
            <tr>
              <td style="padding: 8px 12px 8px 0; color: #374151; font-weight: 600; white-space: nowrap; vertical-align: top;">E-mail</td>
              <td style="padding: 8px 0;">
                <a href="mailto:{safe_email}" style="color: #1d4ed8; text-decoration: none;">{safe_email}</a>
              </td>
            </tr>
            <tr>
              <td style="padding: 8px 12px 8px 0; color: #374151; font-weight: 600; white-space: nowrap; vertical-align: top;">Empresa / Label</td>
              <td style="padding: 8px 0;">{safe_company}</td>
            </tr>
            <tr>
              <td style="padding: 8px 12px 8px 0; color: #374151; font-weight: 600; white-space: nowrap; vertical-align: top;">Perfil</td>
              <td style="padding: 8px 0;">{safe_role}</td>
            </tr>
          </tbody>
        </table>

        <p style="margin-bottom: 8px; font-weight: 600;">Contexto</p>
        <p style="margin-top: 0; padding: 16px; background: #f9fafb; border-radius: 8px; border-left: 3px solid #111827;">
          {safe_message}
        </p>
        """
    )

    try:
        _post_resend(
            to_email=CONTACT_NOTIFICATION_EMAIL,
            subject=f"Sunbeat — Novo contato de {payload.name}",
            html=html,
        )
        logger.info("Contact notification sent for %s", payload.email)
    except Exception as exc:
        logger.error("Failed to send contact notification: %s", exc)
        # Do not expose internal errors to the client
        return {"ok": False, "error": "Erro ao enviar. Tente novamente em instantes."}

    return {"ok": True}
