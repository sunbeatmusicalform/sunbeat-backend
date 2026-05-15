"""
Airtable sync para workflow company_registry.

Tabela alvo: [V2] - Empresas.
Configuravel via extra_settings.airtable.company_registry_table_override ou
AIRTABLE_COMPANY_REGISTRY_TABLE_ID.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pyairtable import Api

from app.core.config import settings
from app.services.workspace_config import get_airtable_extra_config
from app.schemas.submission import CompanyRegistrySubmissionPayload

logger = logging.getLogger(__name__)

COMPANY_REGISTRY_V2_TABLE = "[V2] - Empresas"


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


def _yes_no(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"yes", "no"} else None


def _resolve_table_name(airtable_extra: Dict[str, Any]) -> Optional[str]:
    return (
        airtable_extra.get("company_registry_table_override")
        or getattr(settings, "AIRTABLE_COMPANY_REGISTRY_TABLE_ID", None)
        or COMPANY_REGISTRY_V2_TABLE
    )


def _clean_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                continue
        cleaned[key] = value
    return cleaned


def _build_public_fields(payload: CompanyRegistrySubmissionPayload) -> Dict[str, Any]:
    """
    Constroi o dict de campos PUBLICOS para o Airtable.

    Inclui apenas colunas existentes em [V2] - Empresas: identificacao da
    empresa, endereco, responsaveis e dados bancarios.
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
        "Nome fantasia": cd.fantasy_name,
        "Tipo de documento": cd.document_type,
        "Número do documento": cd.document_number,
        "Razão social": cd.legal_name,
        # Endereco
        "Endereço": cd.address,
        "Cidade": cd.city,
        "Estado": cd.state,
        "CEP": cd.zip_code,
        # Responsavel legal
        "Nome do responsável legal": legal.name,
        "Telefone do responsável legal": str(legal.phone),
        "E-mail do responsável legal": str(legal.email),
        # Responsavel pelo contrato (com fallback para legal)
        "Mesmo que o responsável legal? (Contrato)": _yes_no(
            getattr(contract, "same_as_legal", None)
        ),
        "Nome do responsável pelo contrato": contract_name,
        "Telefone do responsável pelo contrato": contract_phone,
        "E-mail do responsável pelo contrato": contract_email,
        # Responsavel financeiro (com fallback para legal/contrato)
        "Mesmo que o responsável legal? (Financeiro)": _yes_no(
            getattr(financial, "same_as_legal", None)
        ),
        "Mesmo que o responsável pelo contrato?": _yes_no(
            getattr(financial, "same_as_contract", None)
        ),
        "Nome do responsável financeiro": financial_name,
        "Telefone do responsável financeiro": financial_phone,
        "E-mail do responsável financeiro": financial_email,
        # Dados bancarios
        "Banco": bank.bank_name,
        "Agência": bank.agency,
        "Conta": bank.account,
        "Tipo de conta": bank.account_type,
    }

    if bank.pix_key:
        fields["Chave Pix"] = bank.pix_key

    return _clean_fields(fields)


def sync_company_registry_to_airtable(
    payload: CompanyRegistrySubmissionPayload,
) -> Dict[str, Any]:
    """
    Cria um registro na tabela [V2] - Empresas do Airtable.

    Sincroniza os campos publicos suportados pela tabela V2.
    Retorna dict com ok, status, record_id.
    """
    _at_extra = get_airtable_extra_config(str(payload.workspace_slug or ""), "company_registry")
    base_id = _at_extra.get("base_id_override") or getattr(settings, "AIRTABLE_BASE_ID", None)
    table_id = _resolve_table_name(_at_extra)
    enabled = getattr(settings, "AIRTABLE_COMPANY_REGISTRY_ENABLED", False)

    if not enabled:
        logger.info(
            "[company_registry] AIRTABLE_COMPANY_REGISTRY_ENABLED=false — sync pulado"
        )
        return {"ok": True, "status": "disabled"}

    if not base_id or not table_id:
        logger.warning(
            "[company_registry] AIRTABLE_BASE_ID ou tabela do Company Registry "
            "nao configurados — sync pulado"
        )
        return {"ok": True, "status": "not_configured"}

    client = _get_airtable_client()
    if not client:
        return {"ok": False, "status": "no_api_key"}

    # Campos publicos (identificacao, endereco, responsaveis, bancarios)
    fields = _build_public_fields(payload)

    try:
        table = client.table(base_id, table_id)
        record = table.create(fields)
        record_id = record.get("id", "")
        logger.info(
            "[company_registry] Airtable record criado: %s | empresa: %s",
            record_id,
            payload.company_data.legal_name,
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
    Atualiza um registro existente na tabela [V2] - Empresas do Airtable.
    Usado no fluxo de edit/resubmit. Sincroniza apenas campos publicos.
    Nao cria registro novo — exige airtable_record_id valido.
    """
    _at_extra = get_airtable_extra_config(str(payload.workspace_slug or ""), "company_registry")
    base_id = _at_extra.get("base_id_override") or getattr(settings, "AIRTABLE_BASE_ID", None)
    table_id = _resolve_table_name(_at_extra)
    enabled = getattr(settings, "AIRTABLE_COMPANY_REGISTRY_ENABLED", False)

    if not enabled:
        logger.info(
            "[company_registry] AIRTABLE_COMPANY_REGISTRY_ENABLED=false — update pulado"
        )
        return {"ok": True, "status": "disabled"}

    if not base_id or not table_id:
        logger.warning(
            "[company_registry] AIRTABLE_BASE_ID ou tabela do Company Registry "
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
            payload.company_data.legal_name,
        )
        return {"ok": True, "status": "updated", "record_id": airtable_record_id}
    except Exception as exc:
        logger.error("[company_registry] Erro ao atualizar record no Airtable: %s", exc)
        return {"ok": False, "status": "error", "error": str(exc)}
