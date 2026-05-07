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


def _build_public_fields(payload: CompanyRegistrySubmissionPayload) -> Dict[str, Any]:
    """
    Constroi o dict de campos PUBLICOS para o Airtable.

    Inclui: identificacao da empresa, endereco, responsaveis (legal, contrato,
    financeiro), dados bancarios e workspace_slug.

    NAO inclui campos submit-only (draft_token, meta.submitted_at) nem campos
    internos da operacao (datas de contrato, status, financeiro interno etc.).
    Campos submit-only sao adicionados apenas em sync_company_registry_to_airtable().
    Campos internos nunca entram no payload publico.
    """
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
        # Responsavel pelo contrato (com fallback para legal)
        "Resp. Contrato - Nome": contract_name,
        "Resp. Contrato - Telefone": contract_phone,
        "Resp. Contrato - Email": contract_email,
        # Responsavel financeiro (com fallback para legal/contrato)
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
    }

    if bank.pix_key:
        fields["Chave Pix"] = bank.pix_key

    return fields


def sync_company_registry_to_airtable(
    payload: CompanyRegistrySubmissionPayload,
) -> Dict[str, Any]:
    """
    Cria um registro na tabela Company Registry do Airtable.

    Sincroniza campos publicos + campos submit-only (draft_token, submitted_at).
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

    # Campos publicos (identificacao, endereco, responsaveis, bancarios)
    fields = _build_public_fields(payload)

    # Campos submit-only: gravados apenas no POST inicial, nunca no edit/resubmit
    fields["Draft token"] = payload.draft_token
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


def update_company_registry_in_airtable(
    payload: CompanyRegistrySubmissionPayload,
    airtable_record_id: str,
) -> Dict[str, Any]:
    """
    Atualiza um registro existente na tabela Company Registry do Airtable.
    Usado no fluxo de edit/resubmit. Sincroniza apenas campos publicos.
    Nao cria registro novo — exige airtable_record_id valido.
    """
    base_id = getattr(settings, "AIRTABLE_BASE_ID", None)
    table_id = getattr(settings, "AIRTABLE_COMPANY_REGISTRY_TABLE_ID", None)
    enabled = getattr(settings, "AIRTABLE_COMPANY_REGISTRY_ENABLED", False)

    if not enabled:
        logger.info(
            "[company_registry] AIRTABLE_COMPANY_REGISTRY_ENABLED=false — update pulado"
        )
        return {"ok": True, "status": "disabled"}

    if not base_id or not table_id:
        logger.warning(
            "[company_registry] AIRTABLE_BASE_ID ou AIRTABLE_COMPANY_REGISTRY_TABLE_ID "
            "nao configurados — update pulado"
        )
        return {"ok": True, "status": "not_configured"}

    if not airtable_record_id:
        logger.warning("[company_registry] airtable_record_id ausente — update pulado")
        return {"ok": True, "status": "no_record_id"}

    client = _get_airtable_client()
    if not client:
        return {"ok": False, "status": "no_api_key"}

    # Apenas campos publicos — draft_token e submitted_at sao submit-only e nao
    # devem ser sobrescritos no edit. Campos internos nunca entram aqui.
    fields = _build_public_fields(payload)

    try:
        table = client.table(base_id, table_id)
        table.update(airtable_record_id, fields)
        logger.info(
            "[company_registry] Airtable record atualizado: %s | empresa: %s",
            airtable_record_id,
            cd.legal_name,
        )
        return {"ok": True, "status": "updated", "record_id": airtable_record_id}
    except Exception as exc:
        logger.error("[company_registry] Erro ao atualizar record no Airtable: %s", exc)
        return {"ok": False, "status": "error", "error": str(exc)}
