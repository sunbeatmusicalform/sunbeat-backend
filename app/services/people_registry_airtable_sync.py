from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.services.workspace_config import get_airtable_extra_config
from app.core.database import supabase
from app.schemas.people_registry import PeopleRegistryPreparedPayload

AIRTABLE_API_URL = "https://api.airtable.com/v0"
PEOPLE_REGISTRY_TABLE = "people_registry_records"
PEOPLE_REGISTRY_V2_AIRTABLE_TABLE = "[V2] - Pessoas"
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 3
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
ATABAQUE_RESPONSIBLE_COMPANY = "Atabaque"
ATABAQUE_RECEIVED_STATUS = "Informações Recebidas"
PEOPLE_REGISTRY_ROLE_ALIASES = {
    "artist": "artista",
    "producer": "produtor",
    "composer": "compositor",
    "lyricist": "letrista",
    "interpreter": "interprete",
    "partner": "socio",
    "manager": "assessor",
    "label": "gravadora",
    "distributor": "distribuidora",
    "publisher": "editora",
    "contact": "contato",
    "responsible": "contato",
}


@dataclass(frozen=True)
class PeopleRegistryAirtableProfileConfig:
    workspace_slug: str
    workflow_type: str
    profile: str
    table_name: str


@dataclass(frozen=True)
class PeopleRegistryAirtableSyncResult:
    status: str
    base_id: Optional[str] = None
    table_name: Optional[str] = None
    airtable_record_id: Optional[str] = None
    error: Optional[str] = None
    action: Optional[str] = None
    merge_key: Optional[str] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _normalize_roles(roles: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for role in roles:
        value = str(role or "").strip()
        if not value:
            continue
        value = PEOPLE_REGISTRY_ROLE_ALIASES.get(value, value)
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _join_nonempty(parts: List[Optional[str]], separator: str = ", ") -> Optional[str]:
    values = [part for part in parts if part]
    if not values:
        return None
    return separator.join(values)


def _build_address_text(prepared: PeopleRegistryPreparedPayload) -> Optional[str]:
    return _join_nonempty(
        [
            prepared.address.address_line_1,
            prepared.address.address_line_2,
            prepared.address.city,
            prepared.address.state_region,
            prepared.address.postal_code,
            prepared.address.country,
        ]
    )


def _build_notes_text(prepared: PeopleRegistryPreparedPayload) -> Optional[str]:
    roles = ", ".join(prepared.party.roles)
    return _join_nonempty(
        [
            _normalized_text(prepared.additional_info.notes_internal),
            f"Roles: {roles}" if roles else None,
            f"Source: {prepared.meta.source}" if prepared.meta.source else None,
        ],
        separator="\n",
    )


def _airtable_headers() -> Dict[str, str]:
    api_key = (settings.AIRTABLE_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError("Missing required environment variable: AIRTABLE_API_KEY")

    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _airtable_base_id() -> str:
    base_id = (
        (settings.AIRTABLE_PEOPLE_REGISTRY_BASE_ID or "").strip()
        or (settings.AIRTABLE_BASE_ID or "").strip()
    )
    if not base_id:
        raise RuntimeError(
            "Missing required environment variable: AIRTABLE_PEOPLE_REGISTRY_BASE_ID or AIRTABLE_BASE_ID"
        )
    return base_id


def _table_url(base_id: str, table_name: str) -> str:
    return f"{AIRTABLE_API_URL}/{base_id}/{quote(table_name, safe='')}"


def _request_json(
    method: str,
    url: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    headers = _airtable_headers()
    last_error: Optional[str] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                response = client.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    json=payload,
                    params=params,
                )

            if response.status_code in RETRY_STATUS_CODES and attempt < MAX_RETRIES:
                time.sleep(0.8 * attempt)
                continue

            try:
                data: Dict[str, Any] = response.json()
            except Exception:
                data = {"raw": response.text}

            if response.status_code >= 400:
                raise RuntimeError(f"Airtable HTTP {response.status_code}: {data}")

            return data
        except Exception as exc:
            last_error = str(exc)
            if attempt < MAX_RETRIES:
                time.sleep(0.8 * attempt)
                continue

    raise RuntimeError(last_error or "Unknown Airtable error")


def _escape_airtable_formula_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _normalized_document_lookup_formula(document_id: str) -> str:
    normalized_document = _escape_airtable_formula_value(document_id)
    return (
        "SUBSTITUTE("
        "SUBSTITUTE("
        "SUBSTITUTE("
        "SUBSTITUTE({Documento}, '.', ''), "
        "'-', ''), "
        "'/', ''), "
        "' ', '')="
        f"'{normalized_document}'"
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
        if isinstance(value, list) and not value:
            continue

        cleaned[key] = value

    return cleaned


def _atabaque_table_name() -> str:
    return (
        (settings.AIRTABLE_PEOPLE_REGISTRY_ATABAQUE_TABLE or "").strip()
        or PEOPLE_REGISTRY_V2_AIRTABLE_TABLE
    )


def _atabaque_profile_configs() -> List[PeopleRegistryAirtableProfileConfig]:
    table_name = _atabaque_table_name()
    supported_profiles = [
        "atabaque_people_v1",
        "atabaque_cadastro_v1",
        "operational_contact",
    ]
    return [
        PeopleRegistryAirtableProfileConfig(
            workspace_slug="atabaque",
            workflow_type="people_registry",
            profile=profile,
            table_name=table_name,
        )
        for profile in supported_profiles
    ]


def resolve_people_registry_airtable_profile(
    prepared: PeopleRegistryPreparedPayload,
) -> Optional[PeopleRegistryAirtableProfileConfig]:
    for config in _atabaque_profile_configs():
        if (
            config.workspace_slug == prepared.workspace_slug
            and config.workflow_type == prepared.workflow_type
            and config.profile == prepared.profile
        ):
            return config
    return None


def _build_atabaque_airtable_fields(
    *,
    record_id: str,
    prepared: PeopleRegistryPreparedPayload,
) -> Dict[str, Any]:
    del record_id
    is_pf = prepared.party.party_kind == "pf"
    display_name = (
        prepared.party.stage_name
        if is_pf
        else prepared.party.trade_name or prepared.party.display_name
    )
    street_address = _join_nonempty(
        [prepared.address.address_line_1, prepared.address.address_line_2],
        separator="\n",
    )
    notes_text = _build_notes_text(prepared)

    fields: Dict[str, Any] = {
        "Nome de exibição": prepared.party.display_name,
        "Tipo de pessoa": prepared.party.party_kind,
        "Nome legal / Razão social": prepared.party.legal_name,
        "Documento": prepared.party.document_id,
        "Funções": _normalize_roles(prepared.party.roles),
        "E-mail principal": prepared.contact.email_primary,
        "Telefone principal": prepared.contact.phone_primary,
        "Site": prepared.contact.website,
        "Instagram": prepared.contact.instagram,
        "País": prepared.address.country,
        "Estado / Região": prepared.address.state_region,
        "Cidade": prepared.address.city,
        "CEP": prepared.address.postal_code,
        "Endereço": street_address,
        "Chave Pix": prepared.banking.pix_key,
        "Banco": prepared.banking.bank_name,
        "Agência": prepared.banking.bank_agency,
        "Conta": prepared.banking.account_number,
        "Nome do titular da conta": prepared.banking.account_holder_name,
        "Documento do titular da conta": prepared.banking.account_holder_document_id,
        "Nome do empresário / responsável": prepared.additional_info.manager_name,
        "Selo / gravadora": prepared.additional_info.label_name,
        "Observações internas": notes_text,
    }

    if is_pf:
        fields["Nome artístico"] = display_name or prepared.party.display_name
    else:
        fields["Nome fantasia"] = display_name or prepared.party.display_name

    return _clean_fields(fields)


def _find_airtable_record_by_formula(
    *,
    table_url: str,
    formula: str,
) -> Optional[Dict[str, Any]]:
    data = _request_json(
        "GET",
        table_url,
        params={"filterByFormula": formula, "maxRecords": "1"},
    )
    records = data.get("records", [])
    return records[0] if records else None


def _find_existing_airtable_record(
    *,
    table_url: str,
    prepared: PeopleRegistryPreparedPayload,
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    document_id = _normalized_text(prepared.party.document_id)
    if document_id:
        formula = _normalized_document_lookup_formula(document_id)
        record = _find_airtable_record_by_formula(table_url=table_url, formula=formula)
        if record:
            return record, "document_id"

    email_primary = _normalized_text(prepared.contact.email_primary)
    if email_primary:
        lowered_email = _escape_airtable_formula_value(email_primary.lower())
        formula = f"LOWER({{E-mail principal}})='{lowered_email}'"
        record = _find_airtable_record_by_formula(table_url=table_url, formula=formula)
        if record:
            return record, "email_primary"

    return None, None


def _create_airtable_record(
    *,
    table_url: str,
    fields: Dict[str, Any],
) -> Dict[str, Any]:
    data = _request_json(
        "POST",
        table_url,
        payload={"records": [{"fields": fields}], "typecast": True},
    )
    records = data.get("records", [])
    if not records:
        raise RuntimeError("Airtable people registry record was not created.")
    return records[0]


def _update_airtable_record(
    *,
    table_url: str,
    record_id: str,
    fields: Dict[str, Any],
) -> Dict[str, Any]:
    return _request_json(
        "PATCH",
        f"{table_url}/{record_id}",
        payload={"fields": fields, "typecast": True},
    )


def _update_local_sync_state(
    *,
    record_id: str,
    status: str,
    error: Optional[str],
    base_id: Optional[str],
    table_name: Optional[str],
    airtable_record_id: Optional[str],
) -> None:
    supabase.table(PEOPLE_REGISTRY_TABLE).update(
        {
            "airtable_sync_status": status,
            "airtable_sync_error": error,
            "airtable_base_id": base_id,
            "airtable_table_name": table_name,
            "airtable_record_id": airtable_record_id,
            "updated_at": _utc_now_iso(),
        }
    ).eq("id", record_id).execute()


def _blocked_result(
    *,
    record_id: str,
    error: str,
    base_id: Optional[str] = None,
    table_name: Optional[str] = None,
) -> PeopleRegistryAirtableSyncResult:
    _update_local_sync_state(
        record_id=record_id,
        status="blocked",
        error=error,
        base_id=base_id,
        table_name=table_name,
        airtable_record_id=None,
    )
    return PeopleRegistryAirtableSyncResult(
        status="blocked",
        error=error,
        base_id=base_id,
        table_name=table_name,
        action="skipped",
    )


def sync_people_registry_record_to_airtable(
    *,
    record_id: str,
    prepared: PeopleRegistryPreparedPayload,
) -> PeopleRegistryAirtableSyncResult:
    if not settings.AIRTABLE_PEOPLE_REGISTRY_SYNC_ENABLED:
        return _blocked_result(
            record_id=record_id,
            error="People Registry Airtable sync is disabled.",
        )

    config = resolve_people_registry_airtable_profile(prepared)
    if not config:
        return _blocked_result(
            record_id=record_id,
            error=(
                "No Airtable mapping configured for "
                f"workspace={prepared.workspace_slug}, "
                f"workflow_type={prepared.workflow_type}, "
                f"profile={prepared.profile}."
            ),
        )

    if not settings.AIRTABLE_PEOPLE_REGISTRY_ATABAQUE_ENABLED:
        return _blocked_result(
            record_id=record_id,
            error="Atabaque People Registry Airtable sync is disabled.",
            table_name=config.table_name,
        )

    try:
        _at_extra = get_airtable_extra_config(prepared.workspace_slug, "people_registry")
        base_id = _at_extra.get("base_id_override") or _airtable_base_id()
        _table_name = _at_extra.get("people_registry_table_override") or config.table_name
        table_url = _table_url(base_id, _table_name)
        existing_record, merge_key = _find_existing_airtable_record(
            table_url=table_url,
            prepared=prepared,
        )
        fields = _build_atabaque_airtable_fields(
            record_id=record_id,
            prepared=prepared,
        )

        if existing_record:
            airtable_record = _update_airtable_record(
                table_url=table_url,
                record_id=str(existing_record["id"]),
                fields=fields,
            )
            action = "updated"
        else:
            airtable_record = _create_airtable_record(
                table_url=table_url,
                fields=fields,
            )
            action = "created"

        airtable_record_id = str(airtable_record.get("id") or "")
        _update_local_sync_state(
            record_id=record_id,
            status="synced",
            error=None,
            base_id=base_id,
            table_name=_table_name,
            airtable_record_id=airtable_record_id,
        )
        return PeopleRegistryAirtableSyncResult(
            status="synced",
            base_id=base_id,
            table_name=_table_name,
            airtable_record_id=airtable_record_id,
            error=None,
            action=action,
            merge_key=merge_key,
        )
    except Exception as exc:
        base_id = (
            (settings.AIRTABLE_PEOPLE_REGISTRY_BASE_ID or "").strip()
            or (settings.AIRTABLE_BASE_ID or "").strip()
            or None
        )
        error = str(exc)
        _update_local_sync_state(
            record_id=record_id,
            status="failed",
            error=error,
            base_id=base_id,
            table_name=config.table_name,  # config.table_name usado no except (pre-override)
            airtable_record_id=None,
        )
        return PeopleRegistryAirtableSyncResult(
            status="failed",
            base_id=base_id,
            table_name=config.table_name,
            airtable_record_id=None,
            error=error,
            action="failed",
        )
