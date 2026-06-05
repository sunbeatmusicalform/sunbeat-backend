from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4, UUID

from app.core.database import supabase

from app.schemas.people_registry import (
    PeopleRegistryErrorDetailPayload,
    PeopleRegistryLookupItemPayload,
    PeopleRegistryLookupResponsePayload,
    PeopleRegistryPayload,
    PeopleRegistryPreparedPayload,
    PeopleRegistryRecordPayload,
    PeopleRegistryResponsePayload,
    PeopleRegistryValidationIssuePayload,
)
from app.services.workspace_config import get_workflow_settings
from app.services.people_registry_airtable_sync import (
    sync_people_registry_record_to_airtable,
)

SLUG_TEXT_PATTERN = re.compile(r"[^a-z0-9_-]+")
DOCUMENT_PATTERN = re.compile(r"[^A-Za-z0-9]+")
PHONE_PATTERN = re.compile(r"[^\d+]+")
PEOPLE_REGISTRY_TABLE = "people_registry_records"
PEOPLE_LOOKUP_MIN_QUERY_LENGTH = 2
PEOPLE_LOOKUP_MAX_LIMIT = 10
PEOPLE_LOOKUP_CANDIDATE_LIMIT = 50


def _normalized_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _normalized_slug(value: Any) -> Optional[str]:
    text = _normalized_text(value)
    if not text:
        return None

    normalized = SLUG_TEXT_PATTERN.sub("_", text.lower()).strip("_")
    return normalized or None


def _normalized_document_id(value: Any) -> Optional[str]:
    text = _normalized_text(value)
    if not text:
        return None

    normalized = DOCUMENT_PATTERN.sub("", text).upper()
    return normalized or None


def _normalized_phone(value: Any) -> Optional[str]:
    text = _normalized_text(value)
    if not text:
        return None

    normalized = PHONE_PATTERN.sub("", text)
    if normalized.count("+") > 1:
        normalized = normalized.replace("+", "")
    if "+" in normalized and not normalized.startswith("+"):
        normalized = normalized.replace("+", "")

    return normalized or None


def _normalized_email(value: Any) -> Optional[str]:
    text = _normalized_text(value)
    return text.lower() if text else None


def _normalized_lookup_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return text.replace("%", "").replace("_", "").replace("\\", "")


def _normalized_roles(values: Iterable[Any]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()

    for item in values:
        role = _normalized_slug(item)
        if not role or role in seen:
            continue
        seen.add(role)
        normalized.append(role)

    return normalized


def _normalized_lookup_roles(value: Any) -> List[str]:
    if value is None:
        return []

    if isinstance(value, str):
        values = value.split(",")
    else:
        values = value

    return _normalized_roles(values)


def _bounded_lookup_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = PEOPLE_LOOKUP_MAX_LIMIT

    if parsed < 1:
        return 1

    return min(parsed, PEOPLE_LOOKUP_MAX_LIMIT)


def _build_people_lookup_id(row: Dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(row.get("workspace_slug") or ""),
            str(row.get("id") or ""),
            str(row.get("display_name") or ""),
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"people_lookup_{digest}"


def _lookup_confidence(display_name: str, query: str) -> str:
    return "exact" if _normalized_lookup_text(display_name) == query else "partial"


def _issue(field: str, message: str) -> PeopleRegistryValidationIssuePayload:
    return PeopleRegistryValidationIssuePayload(field=field, message=message)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_first_row(result: Any) -> Optional[Dict[str, Any]]:
    rows = getattr(result, "data", None)
    if isinstance(rows, list):
        return rows[0] if rows else None
    if isinstance(rows, dict):
        return rows
    return None


def build_people_registry_record_payload_from_row(
    row: Dict[str, Any],
) -> PeopleRegistryRecordPayload:
    return PeopleRegistryRecordPayload(
        record_id=str(row.get("id") or ""),
        airtable_sync_status=str(row.get("airtable_sync_status") or "pending"),
        edit_token=row.get("edit_token") or None,
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


def build_people_registry_prepared_payload_from_row(
    row: Dict[str, Any],
) -> PeopleRegistryPreparedPayload:
    stored_payload = row.get("payload") or {}
    return PeopleRegistryPreparedPayload.model_validate(stored_payload)


def normalize_people_registry_payload(
    payload: PeopleRegistryPayload,
) -> PeopleRegistryPreparedPayload:
    workspace_slug = _normalized_slug(payload.workspace_slug) or ""
    workflow_type = _normalized_slug(payload.workflow_type) or "people_registry"
    profile = _normalized_slug(payload.profile) or ""

    party_data = payload.party.model_dump()
    party = payload.party.model_copy(
        update={
            "display_name": _normalized_text(party_data.get("display_name")) or "",
            "legal_name": _normalized_text(party_data.get("legal_name")) or "",
            "stage_name": _normalized_text(party_data.get("stage_name")),
            "trade_name": _normalized_text(party_data.get("trade_name")),
            "document_id": _normalized_document_id(party_data.get("document_id")),
            "roles": _normalized_roles(party_data.get("roles") or []),
        }
    )

    contact_data = payload.contact.model_dump()
    contact = payload.contact.model_copy(
        update={
            "email_primary": _normalized_email(contact_data.get("email_primary")),
            "phone_primary": _normalized_phone(contact_data.get("phone_primary")),
            "website": _normalized_text(contact_data.get("website")),
            "instagram": _normalized_text(contact_data.get("instagram")),
        }
    )

    address_data = payload.address.model_dump()
    address = payload.address.model_copy(
        update={
            "country": _normalized_text(address_data.get("country")),
            "state_region": _normalized_text(address_data.get("state_region")),
            "city": _normalized_text(address_data.get("city")),
            "postal_code": _normalized_text(address_data.get("postal_code")),
            "address_line_1": _normalized_text(address_data.get("address_line_1")),
            "address_line_2": _normalized_text(address_data.get("address_line_2")),
        }
    )

    banking_data = payload.banking.model_dump()
    banking = payload.banking.model_copy(
        update={
            "pix_key": _normalized_text(banking_data.get("pix_key")),
            "bank_name": _normalized_text(banking_data.get("bank_name")),
            "bank_agency": _normalized_text(banking_data.get("bank_agency")),
            "account_number": _normalized_text(banking_data.get("account_number")),
            "account_holder_name": _normalized_text(
                banking_data.get("account_holder_name")
            ),
            "account_holder_document_id": _normalized_document_id(
                banking_data.get("account_holder_document_id")
            ),
        }
    )

    additional_info_data = payload.additional_info.model_dump()
    additional_info = payload.additional_info.model_copy(
        update={
            "manager_name": _normalized_text(additional_info_data.get("manager_name")),
            "label_name": _normalized_text(additional_info_data.get("label_name")),
            "notes_internal": _normalized_text(
                additional_info_data.get("notes_internal")
            ),
            "external_refs": dict(additional_info_data.get("external_refs") or {}),
        }
    )

    meta_data = payload.meta.model_dump()
    form_version = _normalized_slug(meta_data.get("form_version")) or "draft_v1"
    meta = payload.meta.model_copy(
        update={
            "form_version": form_version,
            "source": _normalized_text(meta_data.get("source")),
            "submitted_at": _normalized_text(meta_data.get("submitted_at")),
        }
    )

    return PeopleRegistryPreparedPayload(
        workspace_slug=workspace_slug,
        workflow_type=workflow_type,
        profile=profile,
        party=party,
        contact=contact,
        address=address,
        banking=banking,
        additional_info=additional_info,
        meta=meta,
        normalized={
            "workspace_slug": workspace_slug,
            "workflow_type": workflow_type,
            "profile": profile,
            "party_kind": party.party_kind,
            "document_id": party.document_id,
            "email_primary": contact.email_primary,
            "phone_primary": contact.phone_primary,
            "roles": list(party.roles),
            "form_version": str(meta.form_version or ""),
            "source": meta.source,
        },
    )


def validate_people_registry_payload(
    prepared: PeopleRegistryPreparedPayload,
) -> List[PeopleRegistryValidationIssuePayload]:
    issues: List[PeopleRegistryValidationIssuePayload] = []

    if not prepared.workspace_slug:
        issues.append(_issue("workspace_slug", "workspace_slug is required."))

    if prepared.workflow_type != "people_registry":
        issues.append(
            _issue(
                "workflow_type",
                "workflow_type must resolve to people_registry.",
            )
        )

    if not prepared.profile:
        issues.append(_issue("profile", "profile is required."))

    if not prepared.party.display_name.strip():
        issues.append(_issue("party.display_name", "display_name is required."))

    if not prepared.party.legal_name.strip():
        issues.append(_issue("party.legal_name", "legal_name is required."))

    if not prepared.party.roles:
        issues.append(_issue("party.roles", "At least one role is required."))

    if not (prepared.party.document_id or prepared.contact.email_primary):
        issues.append(
            _issue(
                "party.document_id",
                "Provide document_id or contact.email_primary.",
            )
        )

    if prepared.party.party_kind == "pf" and prepared.party.trade_name:
        issues.append(
            _issue(
                "party.trade_name",
                "trade_name is not expected for party_kind=pf in this phase.",
            )
        )

    if prepared.party.party_kind == "pj" and prepared.party.stage_name:
        issues.append(
            _issue(
                "party.stage_name",
                "stage_name is not expected for party_kind=pj in this phase.",
            )
        )

    return issues


def build_people_registry_error_detail(
    *,
    code: str,
    message: str,
    stage: str,
    issues: Optional[List[PeopleRegistryValidationIssuePayload]] = None,
) -> PeopleRegistryErrorDetailPayload:
    return PeopleRegistryErrorDetailPayload(
        code=code,
        message=message,
        stage=stage,
        issues=issues or [],
    )


def build_people_registry_response(
    payload: PeopleRegistryPayload,
) -> PeopleRegistryResponsePayload:
    prepared = normalize_people_registry_payload(payload)
    issues = validate_people_registry_payload(prepared)
    prepared.validation_issues = issues

    if issues:
        return PeopleRegistryResponsePayload(
            ok=False,
            status="invalid",
            data=prepared,
            error=build_people_registry_error_detail(
                code="people_registry_validation_failed",
                message="People registry payload validation failed.",
                stage="people_registry_validation",
                issues=issues,
            ),
        )

    return PeopleRegistryResponsePayload(
        ok=True,
        status="validated",
        data=prepared,
        record=None,
        error=None,
    )


def build_people_registry_insert_row(
    prepared: PeopleRegistryPreparedPayload,
) -> Dict[str, Any]:
    now_iso = _utc_now_iso()
    record_id = str(uuid4())

    return {
        "id": record_id,
        "workspace_slug": prepared.workspace_slug,
        "workflow_type": prepared.workflow_type,
        "form_version": str(prepared.meta.form_version or ""),
        "profile": prepared.profile,
        "source": prepared.meta.source,
        "party_kind": prepared.party.party_kind,
        "display_name": prepared.party.display_name,
        "legal_name": prepared.party.legal_name,
        "stage_name": prepared.party.stage_name,
        "trade_name": prepared.party.trade_name,
        "document_id": prepared.party.document_id,
        "email_primary": prepared.contact.email_primary,
        "phone_primary": prepared.contact.phone_primary,
        "country": prepared.address.country,
        "state_region": prepared.address.state_region,
        "city": prepared.address.city,
        "roles_json": list(prepared.party.roles),
        "payload": prepared.model_dump(mode="json"),
        "airtable_sync_status": "pending",
        "airtable_sync_error": None,
        "airtable_base_id": None,
        "airtable_table_name": None,
        "airtable_record_id": None,
        "edit_token": str(uuid4()),
        "created_at": now_iso,
        "updated_at": now_iso,
    }


def find_people_registry_duplicate_record(
    prepared: PeopleRegistryPreparedPayload,
) -> Optional[tuple[Dict[str, Any], str]]:
    if prepared.party.document_id:
        result = (
            supabase.table(PEOPLE_REGISTRY_TABLE)
            .select("*")
            .eq("workspace_slug", prepared.workspace_slug)
            .eq("document_id", prepared.party.document_id)
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )
        row = _extract_first_row(result)
        if row:
            return row, "party.document_id"

    if not prepared.party.document_id and prepared.contact.email_primary:
        result = (
            supabase.table(PEOPLE_REGISTRY_TABLE)
            .select("*")
            .eq("workspace_slug", prepared.workspace_slug)
            .eq("email_primary", prepared.contact.email_primary)
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )
        row = _extract_first_row(result)
        if row:
            return row, "contact.email_primary"

    return None


def lookup_people_registry_records(
    workspace_slug: str,
    query: str,
    roles: Optional[str] = None,
    limit: Optional[int] = None,
) -> PeopleRegistryLookupResponsePayload:
    normalized_workspace = _normalized_slug(workspace_slug) or ""
    normalized_query = _normalized_lookup_text(query)
    requested_roles = _normalized_lookup_roles(roles)
    bounded_limit = _bounded_lookup_limit(limit)

    if (
        not normalized_workspace
        or len(normalized_query) < PEOPLE_LOOKUP_MIN_QUERY_LENGTH
    ):
        return PeopleRegistryLookupResponsePayload(ok=True, items=[])

    result = (
        supabase.table(PEOPLE_REGISTRY_TABLE)
        .select("id, workspace_slug, display_name, roles_json")
        .eq("workspace_slug", normalized_workspace)
        .ilike("display_name", f"%{normalized_query}%")
        .order("display_name", desc=False)
        .limit(PEOPLE_LOOKUP_CANDIDATE_LIMIT)
        .execute()
    )

    rows = getattr(result, "data", None)
    if not isinstance(rows, list):
        return PeopleRegistryLookupResponsePayload(ok=True, items=[])

    items: List[PeopleRegistryLookupItemPayload] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        display_name = str(row.get("display_name") or "").strip()
        if not display_name:
            continue

        row_roles = _normalized_lookup_roles(row.get("roles_json") or [])
        if requested_roles and not set(requested_roles).intersection(row_roles):
            continue

        items.append(
            PeopleRegistryLookupItemPayload(
                id=_build_people_lookup_id(row),
                displayName=display_name,
                roles=row_roles,
                source="people_registry",
                confidence=_lookup_confidence(display_name, normalized_query),
            )
        )

        if len(items) >= bounded_limit:
            break

    return PeopleRegistryLookupResponsePayload(ok=True, items=items)


def fetch_people_registry_record_by_id(record_id: str) -> Optional[Dict[str, Any]]:
    result = (
        supabase.table(PEOPLE_REGISTRY_TABLE)
        .select("*")
        .eq("id", record_id)
        .limit(1)
        .execute()
    )
    return _extract_first_row(result)


def fetch_people_registry_record_by_edit_token(edit_token: str) -> Optional[Dict[str, Any]]:
    result = (
        supabase.table(PEOPLE_REGISTRY_TABLE)
        .select("*")
        .eq("edit_token", edit_token)
        .limit(1)
        .execute()
    )
    return _extract_first_row(result)


def update_people_registry_record(
    record_id: str,
    prepared: "PeopleRegistryPreparedPayload",
) -> Optional[Dict[str, Any]]:
    now_iso = _utc_now_iso()
    update_fields: Dict[str, Any] = {
        "party_kind": prepared.party.party_kind,
        "display_name": prepared.party.display_name,
        "legal_name": prepared.party.legal_name,
        "stage_name": prepared.party.stage_name,
        "trade_name": prepared.party.trade_name,
        "document_id": prepared.party.document_id,
        "email_primary": prepared.contact.email_primary,
        "phone_primary": prepared.contact.phone_primary,
        "country": prepared.address.country,
        "state_region": prepared.address.state_region,
        "city": prepared.address.city,
        "roles_json": list(prepared.party.roles),
        "payload": prepared.model_dump(mode="json"),
        "updated_at": now_iso,
    }
    result = (
        supabase.table(PEOPLE_REGISTRY_TABLE)
        .update(update_fields)
        .eq("id", record_id)
        .execute()
    )
    return _extract_first_row(result)


def persist_people_registry_prepared_payload(
    prepared: PeopleRegistryPreparedPayload,
) -> PeopleRegistryRecordPayload:
    row = build_people_registry_insert_row(prepared)
    result = supabase.table(PEOPLE_REGISTRY_TABLE).insert(row).execute()
    created = _extract_first_row(result)

    if not created:
        raise RuntimeError("People registry record was not persisted.")

    return PeopleRegistryRecordPayload(
        record_id=str(created.get("id") or row["id"]),
        airtable_sync_status=str(
            created.get("airtable_sync_status") or row["airtable_sync_status"]
        ),
        created_at=str(created.get("created_at") or row["created_at"]),
        updated_at=str(created.get("updated_at") or row["updated_at"]),
    )


def create_people_registry_record_response(
    payload: PeopleRegistryPayload,
) -> PeopleRegistryResponsePayload:
    validation_response = build_people_registry_response(payload)
    if not validation_response.ok or not validation_response.data:
        return validation_response

    prepared = validation_response.data

    try:
        duplicate = find_people_registry_duplicate_record(prepared)
    except Exception as exc:
        return PeopleRegistryResponsePayload(
            ok=False,
            status="error",
            data=prepared,
            record=None,
            error=build_people_registry_error_detail(
                code="people_registry_duplicate_lookup_failed",
                message=f"Could not check people registry duplicates: {exc}",
                stage="people_registry_duplicate_lookup",
                issues=[],
            ),
        )

    if duplicate:
        duplicate_row, duplicate_field = duplicate
        duplicate_value = prepared.party.document_id or prepared.contact.email_primary
        return PeopleRegistryResponsePayload(
            ok=False,
            status="conflict",
            data=prepared,
            record=build_people_registry_record_payload_from_row(duplicate_row),
            error=build_people_registry_error_detail(
                code="people_registry_duplicate_conflict",
                message="A matching people registry record already exists in this workspace.",
                stage="people_registry_duplicate_lookup",
                issues=[
                    _issue(
                        duplicate_field,
                        f"Duplicate match found for {duplicate_field}={duplicate_value}.",
                    )
                ],
            ),
        )

    try:
        record = persist_people_registry_prepared_payload(prepared)
    except Exception as exc:
        return PeopleRegistryResponsePayload(
            ok=False,
            status="error",
            data=prepared,
            record=None,
            error=build_people_registry_error_detail(
                code="people_registry_persistence_failed",
                message=f"Could not persist people registry record: {exc}",
                stage="people_registry_persistence",
                issues=[],
            ),
        )

    _pr_cfg = get_workflow_settings(prepared.workspace_slug, "people_registry")
    if _pr_cfg.get("airtable_sync_enabled", True):
        try:
            sync_people_registry_record_to_airtable(
                record_id=record.record_id,
                prepared=prepared,
            )
        except Exception:
            # Preserve Supabase as the canonical write even if the Airtable hook fails unexpectedly.
            pass
    else:
        logger.info(
            "Airtable sync skipped by workspace config people_registry record_id=%s workspace=%s",
            record.record_id,
            prepared.workspace_slug,
        )

    try:
        persisted_row = fetch_people_registry_record_by_id(record.record_id)
        if persisted_row:
            record = build_people_registry_record_payload_from_row(persisted_row)
    except Exception:
        pass

    return PeopleRegistryResponsePayload(
        ok=True,
        status="created",
        data=prepared,
        record=record,
        error=None,
    )


def get_people_registry_record_response(record_id: str) -> PeopleRegistryResponsePayload:
    try:
        row = fetch_people_registry_record_by_id(record_id)
    except Exception as exc:
        return PeopleRegistryResponsePayload(
            ok=False,
            status="error",
            data=None,
            record=None,
            error=build_people_registry_error_detail(
                code="people_registry_fetch_failed",
                message=f"Could not fetch people registry record: {exc}",
                stage="people_registry_fetch",
                issues=[],
            ),
        )

    if not row:
        return PeopleRegistryResponsePayload(
            ok=False,
            status="error",
            data=None,
            record=None,
            error=build_people_registry_error_detail(
                code="people_registry_record_not_found",
                message="People registry record was not found.",
                stage="people_registry_fetch",
                issues=[],
            ),
        )

    return PeopleRegistryResponsePayload(
        ok=True,
        status="fetched",
        data=build_people_registry_prepared_payload_from_row(row),
        record=build_people_registry_record_payload_from_row(row),
        error=None,
    )


def get_people_registry_record_by_edit_token_response(edit_token: str) -> PeopleRegistryResponsePayload:
    try:
        row = fetch_people_registry_record_by_edit_token(edit_token)
    except Exception as exc:
        return PeopleRegistryResponsePayload(
            ok=False,
            status="error",
            data=None,
            record=None,
            error=build_people_registry_error_detail(
                code="people_registry_fetch_failed",
                message=f"Could not fetch people registry record: {exc}",
                stage="people_registry_fetch",
                issues=[],
            ),
        )

    if not row:
        return PeopleRegistryResponsePayload(
            ok=False,
            status="error",
            data=None,
            record=None,
            error=build_people_registry_error_detail(
                code="people_registry_record_not_found",
                message="People registry record was not found.",
                stage="people_registry_fetch",
                issues=[],
            ),
        )

    return PeopleRegistryResponsePayload(
        ok=True,
        status="fetched",
        data=build_people_registry_prepared_payload_from_row(row),
        record=build_people_registry_record_payload_from_row(row),
        error=None,
    )


def update_people_registry_record_response(
    edit_token: str,
    payload: PeopleRegistryPayload,
) -> PeopleRegistryResponsePayload:
    # Validate payload first
    validation_response = build_people_registry_response(payload)
    if not validation_response.ok or not validation_response.data:
        return validation_response

    prepared = validation_response.data

    # Resolve existing record by edit_token
    try:
        existing_row = fetch_people_registry_record_by_edit_token(edit_token)
    except Exception as exc:
        return PeopleRegistryResponsePayload(
            ok=False,
            status="error",
            data=prepared,
            record=None,
            error=build_people_registry_error_detail(
                code="people_registry_fetch_failed",
                message=f"Could not fetch people registry record for edit: {exc}",
                stage="people_registry_edit_lookup",
                issues=[],
            ),
        )

    if not existing_row:
        return PeopleRegistryResponsePayload(
            ok=False,
            status="error",
            data=prepared,
            record=None,
            error=build_people_registry_error_detail(
                code="people_registry_record_not_found",
                message="No people registry record found for this edit_token.",
                stage="people_registry_edit_lookup",
                issues=[],
            ),
        )

    record_id = str(existing_row["id"])

    try:
        update_people_registry_record(record_id, prepared)
    except Exception as exc:
        return PeopleRegistryResponsePayload(
            ok=False,
            status="error",
            data=prepared,
            record=None,
            error=build_people_registry_error_detail(
                code="people_registry_update_failed",
                message=f"Could not update people registry record: {exc}",
                stage="people_registry_update",
                issues=[],
            ),
        )

    _pr_edit_cfg = get_workflow_settings(prepared.workspace_slug, "people_registry")
    if _pr_edit_cfg.get("airtable_sync_enabled", True):
        try:
            sync_people_registry_record_to_airtable(
                record_id=record_id,
                prepared=prepared,
            )
        except Exception:
            # Non-fatal: Supabase is the canonical store; Airtable sync failure
            # must not block the PATCH response.
            pass
    else:
        logger.info(
            "Airtable sync skipped by workspace config people_registry record_id=%s workspace=%s",
            record_id,
            prepared.workspace_slug,
        )

    try:
        updated_row = fetch_people_registry_record_by_id(record_id)
        if updated_row:
            record = build_people_registry_record_payload_from_row(updated_row)
            prepared = build_people_registry_prepared_payload_from_row(updated_row)
        else:
            record = build_people_registry_record_payload_from_row(existing_row)
    except Exception:
        record = build_people_registry_record_payload_from_row(existing_row)

    return PeopleRegistryResponsePayload(
        ok=True,
        status="created",
        data=prepared,
        record=record,
        error=None,
    )
