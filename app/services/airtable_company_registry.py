"""
Airtable sync para workflow company_registry.

Tabela alvo: Company Registry (configuravel via AIRTABLE_COMPANY_REGISTRY_TABLE_ID).
Se a variavel nao estiver definida, o sync e pulado com log de aviso.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pyairtable import Api

from app.core.config import settings
from app.schemas.submission import CompanyRegistrySubmissionPayload

logger = logging.getLogger(__name__)


def _get_airtable_client() -> Optional[Api]:
    token = settings.AIRTABLE_API_KEY
    if not token:
        logger.warning("[company_registry] AIRTABLE_API_KEY nao configurada — sync pulado")
        return None
    return Api(token)


def _resolve_rep_name(
    rep: Any,
    legal: Any,
    contract: Any = None,
) -> str:
    """Resolve nome efetivo do responsavel, respeitando same_as flags."""
    same_as_legal = getattr(rep, "same_as_legal", None)
    same_as_contract = getattr(rep, "same_as_contract", None)

    if same_as_legal == "yes":
        return getattr(legal, "name", "") or ""
    if same_as_contract == "yes" and contract:
        # contract representative can also be same_as_legal
        if getattr(contract, "same_as_legal", None) == "yes":
            return getattr(legal, "name", "") or ""
        return getattr(contract, "name", "") or ""
    return getattr(rep, "name", "") or ""


def _resolve_rep_phone(rep: Any, legal: Any, contract: Any = None) -> str:
    same_as_legal = getattr(rep, "same_as_legal", None)
    same_as_contract = getattr(rep, "same_as_contract", None)
    if same_as_legal == "yes":
        return getattr(legal, "phone", "") or ""
    if same_as_contract == "yes" and contract:
        if getattr(contract, "same_as_legal", None) == "yes":
            return getattr(legal, "phone", "") or ""
        return getattr(contract, "phone", "") or ""
    return getattr(rep, "phone", "") or ""


def _resolve_rep_email(rep: Any, legal: Any, contract: Any = None) -> str:
    same_as_legal = getattr(rep, "same_as_legal", None)
    same_as_contract = getattr(rep, "same_as_contract", None)
    if same_as_legal == "yes":
        return getattr(legal, "email", "") or ""
    if same_as_contract == "yes" and contract:
        if getattr(contract, "same_as_legal", None) == "yes":
            return getattr(legal, "email", "") or ""
        return getattr(contract, "email", "") or ""
    return getattr(rep, "email", "") or ""


def sync_company_registry_to_airtable(
    payload: CompanyRegistrySubmissionPayload,
) -> Dict[str, Any]:
    """
    Cria um registro na tabela Company Registry do Airtable.

    Retorna dict com ok, status, record_id.
    """
    base_id = getattr(settings, "AIRTABLE_BASE_ID", None)
    table_id = getattr(settings, "AIRTABLE_COMPANY_REGISTRY_TABLE_ID", None)
    enabled = getattr(settings, "AIRTABLE_COMPANY_REGISTRY_ENABLED", False)

    if not enabled:
        logger.info(
            "[company_registry] AIRTABLE_COMPANY_REGISTRY_ENABLED=false — sync pulado"
        )
        return {"ok": True, "status": "disabled"}

    if not base_id or not table_id:
        logger.warning(
            "[company_registry] AIRTABLE_BASE_ID ou AIRTABLE_COMPANY_REGISTRY_TABLE_ID "
            "nao configurados — sync pulado"
        )
        return {"ok": True, "status": "not_configured"}

    client = _get_airtable_client()
    if not client:
        return {"ok": False, "status": "no_api_key"}

    cd = payload.company_data
    legal = payload.legal_representative
    contract = payload.contract_representative
    financial = payload.financial_representative
    bank = payload.banking_data

    contract_name = _resolve_rep_name(contract, legal)
    contract_phone = _resolve_rep_phone(contract, legal)
    contract_email = _resolve_rep_email(contract, legal)

    financial_name = _resolve_rep_name(financial, legal, contract)
    financial_phone = _resolve_rep_phone(financial, legal, contract)
    financial_email = _resolve_rep_email(financial, legal, contract)

    fields: Dict[str, Any] = {
        # Identificacao
        "Tipo de documento": cd.document_type.upper(),
        "Numero do documento": cd.document_number,
        "Nome fantasia": cd.fantasy_name,
        "Razao social": cd.legal_name,
        # Endereco
        "Endereco": cd.address,
        "Cidade": cd.city,
        "Estado": cd.state,
        "CEP": cd.zip_code,
        # Responsavel legal
        "Resp. Legal - Nome": legal.name,
        "Resp. Legal - Telefone": str(legal.phone),
        "Resp. Legal - Email": str(legal.email),
        # Responsavel pelo contrato
        "Resp. Contrato - Nome": contract_name,
        "Resp. Contrato - Telefone": contract_phone,
        "Resp. Contrato - Email": contract_email,
        # Responsavel financeiro
        "Resp. Financeiro - Nome": financial_name,
        "Resp. Financeiro - Telefone": financial_phone,
        "Resp. Financeiro - Email": financial_email,
        # Dados bancarios
        "Banco": bank.bank_name,
        "Agencia": bank.agency,
        "Conta": bank.account,
        "Tipo de conta": "Conta corrente" if bank.account_type == "corrente" else "Conta poupanca",
        # Meta
        "Workspace": payload.workspace_slug,
        "Draft token": payload.draft_token,
    }

    if bank.pix_key:
        fields["Chave Pix"] = bank.pix_key

    if payload.meta and payload.meta.submitted_at:
        fields["Enviado em"] = payload.meta.submitted_at

    try:
        table = client.table(base_id, table_id)
        record = table.create(fields)
        record_id = record.get("id", "")
        logger.info(
            "[company_registry] Airtable record criado: %s | empresa: %s",
            record_id,
            cd.legal_name,
        )
        return {"ok": True, "status": "created", "record_id": record_id}
    except Exception as exc:
        logger.error("[company_registry] Erro ao criar record no Airtable: %s", exc)
        return {"ok": False, "status": "error", "error": str(exc)}
