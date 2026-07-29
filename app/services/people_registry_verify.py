from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

from app.core.config import settings
from app.schemas.people_registry import (
    PeopleRegistryVerifyMatchPayload,
    PeopleRegistryVerifyResponsePayload,
)
from app.services.airtable import _request_json
from app.services.workspace_config import get_airtable_extra_config

ATABAQUE_AIRTABLE_BASE_ID = "appGaV0kdkc2NEt0F"
LEGACY_TABLE_DEFAULT = "Dados Cadastrais"
V2_TABLE_DEFAULT = "[V2] - Pessoas"

LEGACY_FIELDS = {
    "names": ("Name - Cadastro", "Nome Completo"),
    "email": "Endereço de e-mail",
    "document": "Idpessoa",
}
V2_FIELDS = {
    "names": ("Nome de Exibição", "Nome Legal / Razão Social", "Nome Artístico", "Nome Fantasia"),
    "email": "E-mail principal",
    "document": "Documento",
}

_DOCUMENT_PATTERN = re.compile(r"\D+")
_SPACE_PATTERN = re.compile(r"\s+")


def _normalized_text(value: Any) -> str:
    return _SPACE_PATTERN.sub(" ", str(value or "").strip())


def _normalized_email(value: Any) -> str:
    return _normalized_text(value).lower()


def _normalized_document(value: Any) -> str:
    return _DOCUMENT_PATTERN.sub("", str(value or ""))


def _normalized_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _normalized_text(value))
    without_marks = "".join(char for char in text if not unicodedata.combining(char))
    return without_marks.casefold()


def _table_url(base_id: str, table_name: str) -> str:
    return f"https://api.airtable.com/v0/{base_id}/{quote(table_name, safe='')}"


def _list_airtable_records(*, base_id: str, table_name: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    offset: Optional[str] = None

    while True:
        params: Dict[str, Any] = {"pageSize": 100}
        if offset:
            params["offset"] = offset

        data = _request_json(
            "GET",
            _table_url(base_id, table_name),
            params=params,
        )
        records.extend(
            record for record in data.get("records", []) if isinstance(record, dict)
        )
        offset = str(data.get("offset") or "").strip() or None
        if not offset:
            return records


def _first_text(fields: Dict[str, Any], names: Iterable[str]) -> Optional[str]:
    for name in names:
        value = _normalized_text(fields.get(name))
        if value:
            return value
    return None


def _match_records(
    *,
    records: Iterable[Dict[str, Any]],
    query: str,
    field_map: Dict[str, Any],
) -> Optional[PeopleRegistryVerifyMatchPayload]:
    normalized_query_email = _normalized_email(query)
    normalized_query_document = _normalized_document(query)
    normalized_query_name = _normalized_name(query)
    rows = [record for record in records if isinstance(record.get("fields"), dict)]

    for match_by in ("email", "documento", "nome"):
        for record in rows:
            fields = record["fields"]
            if match_by == "email":
                matched = bool(
                    normalized_query_email
                    and _normalized_email(fields.get(field_map["email"]))
                    == normalized_query_email
                )
            elif match_by == "documento":
                matched = bool(
                    normalized_query_document
                    and _normalized_document(fields.get(field_map["document"]))
                    == normalized_query_document
                )
            else:
                matched = bool(
                    normalized_query_name
                    and any(
                        _normalized_name(fields.get(field_name)) == normalized_query_name
                        for field_name in field_map["names"]
                    )
                )

            if matched:
                return PeopleRegistryVerifyMatchPayload(
                    record_id=str(record.get("id") or ""),
                    display_name=_first_text(fields, field_map["names"]),
                    match_by=match_by,
                )

    return None


def _resolve_airtable_targets(workspace_slug: str) -> Dict[str, str]:
    extra = get_airtable_extra_config(workspace_slug, "people_registry")
    configured_base = str(
        extra.get("base_id_override")
        or settings.AIRTABLE_PEOPLE_REGISTRY_BASE_ID
        or ""
    ).strip()
    base_id = (
        configured_base
        or ATABAQUE_AIRTABLE_BASE_ID
        if workspace_slug.strip().lower() == "atabaque"
        else configured_base or str(settings.AIRTABLE_BASE_ID or "").strip()
    )
    if not base_id:
        raise RuntimeError("Airtable base is not configured for people verification.")

    return {
        "base_id": base_id,
        "legacy_table": str(
            extra.get("people_registry_legacy_table_override")
            or LEGACY_TABLE_DEFAULT
        ).strip(),
        "v2_table": str(
            extra.get("people_registry_table_override")
            or settings.AIRTABLE_PEOPLE_REGISTRY_ATABAQUE_TABLE
            or V2_TABLE_DEFAULT
        ).strip(),
    }


def verify_people_registry_records(
    *,
    workspace_slug: str,
    query: str,
) -> PeopleRegistryVerifyResponsePayload:
    normalized_query = _normalized_text(query)
    targets = _resolve_airtable_targets(workspace_slug)

    legacy_records = _list_airtable_records(
        base_id=targets["base_id"],
        table_name=targets["legacy_table"],
    )
    v2_records = _list_airtable_records(
        base_id=targets["base_id"],
        table_name=targets["v2_table"],
    )

    legacy_match = _match_records(
        records=legacy_records,
        query=normalized_query,
        field_map=LEGACY_FIELDS,
    )
    v2_match = _match_records(
        records=v2_records,
        query=normalized_query,
        field_map=V2_FIELDS,
    )

    if legacy_match and v2_match:
        verdict, action = "ambas", "usar_v2"
    elif v2_match:
        verdict, action = "so_v2", "usar_v2"
    elif legacy_match:
        verdict, action = "so_legado", "migrar_para_v2"
    else:
        verdict, action = "nao_encontrado", "criar_cadastro"

    return PeopleRegistryVerifyResponsePayload(
        ok=True,
        query=normalized_query,
        verdict=verdict,
        dados_cadastrais=legacy_match,
        v2_pessoas=v2_match,
        acao=action,
    )
