from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Dict, Optional, cast
from urllib.parse import quote
from uuid import uuid4

from app.core.config import settings
from app.core.database import supabase
from app.modules.workflow_registry import build_frontend_workflow_path
from app.schemas.people_registry import (
    PeopleRegistryInviteCreatePayload,
    PeopleRegistryInviteEmailPayload,
    PeopleRegistryInviteEmailResponsePayload,
    PeopleRegistryInviteErrorPayload,
    PeopleRegistryInviteListResponsePayload,
    PeopleRegistryInvitePayload,
    PeopleRegistryInviteResponsePayload,
    PeopleRegistryInviteStatus,
    PeopleRegistryInviteSubmitPayload,
    PeopleRegistryInviteSubmitResponsePayload,
    PeopleRegistryPayload,
    PeopleRegistryPreparedPayload,
    PeopleRegistryResponsePayload,
)
from app.services.airtable import _base_id, _request_json
from app.services.airtable_rights_clearance import CLEARANCE_PARTES_TABLE
from app.services.people_registry import (
    create_people_registry_record_response,
    fetch_people_registry_record_by_id,
    get_people_registry_record_response,
    normalize_people_registry_payload,
)
from app.services.people_registry_documents import format_document_id_for_display
from app.services.email import send_people_registry_invite_email
from app.services.workspace_config import get_airtable_extra_config

PEOPLE_REGISTRY_INVITES_TABLE = "people_registry_invites"

PARTE_CONTEXT_FIELDS = {
    "person_link": "Pessoa Vinculada",
    "registration_status": "Status do Cadastro",
    "communication_channel": "Canal de Comunicação",
    "document": "CPF / CNPJ",
    "email": "E-mail de Assinatura",
    "phone": "Telefone de Assinatura",
    "musical_role": "Função Musical no Clearance",
    "remuneration_type": "Tipo de Remuneração",
    "participation_percent": "Percentual / Participação",
    "fixed_amount": "Valor Fixo da Remuneração",
    "remuneration_notes": "Observações de Remuneração",
    "approval_status": "Status de Aprovação",
}

INVITE_STATUSES: set[str] = {
    "pending",
    "sent",
    "opened",
    "submitted",
    "submitted_pending_airtable",
    "failed",
    "expired",
    "discontinued",
}

CONTESTED_CONFIRMATION_VALUES = {
    "contestou",
    "em_negociacao",
    "em negociação",
    "precisa_revisao",
    "precisa revisão",
    "needs_review",
    "review",
    "disputed",
}

SLUG_TEXT_PATTERN = re.compile(r"[^a-z0-9_-]+")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _normalized_slug(value: Any) -> Optional[str]:
    text = _normalized_text(value)
    if not text:
        return None
    normalized = SLUG_TEXT_PATTERN.sub("_", text.lower()).strip("_")
    return normalized or None


def _extract_first_row(result: Any) -> Optional[Dict[str, Any]]:
    rows = getattr(result, "data", None)
    if isinstance(rows, list):
        return rows[0] if rows else None
    if isinstance(rows, dict):
        return rows
    return None


def _coerce_invite_status(value: Any) -> PeopleRegistryInviteStatus:
    status = str(value or "pending").strip()
    if status in INVITE_STATUSES:
        return cast(PeopleRegistryInviteStatus, status)
    return "pending"


def _invite_error(
    *,
    code: str,
    message: str,
    stage: str,
) -> PeopleRegistryInviteErrorPayload:
    return PeopleRegistryInviteErrorPayload(
        code=code,
        message=message,
        stage=stage,
    )


def _invite_url(*, workspace_slug: str, token: str) -> str:
    frontend_base = (getattr(settings, "FRONTEND_BASE_URL", "") or "https://sunbeat.pro").rstrip("/")
    path = build_frontend_workflow_path(
        workspace_slug=workspace_slug,
        workflow_type="people_registry",
    )
    return f"{frontend_base}{path}?invite={quote(token, safe='')}"


def _invite_payload_from_row(row: Dict[str, Any]) -> PeopleRegistryInvitePayload:
    token = str(row.get("token") or "")
    workspace_slug = str(row.get("workspace_slug") or "")
    return PeopleRegistryInvitePayload(
        token=token,
        status=_coerce_invite_status(row.get("status")),
        workspace_slug=workspace_slug,
        profile=str(row.get("profile") or "atabaque_people_v1"),
        airtable_clearance_part_id=str(row.get("airtable_clearance_part_id") or ""),
        invite_url=_invite_url(workspace_slug=workspace_slug, token=token),
        context=row.get("context") if isinstance(row.get("context"), dict) else {},
        people_registry_record_id=row.get("people_registry_record_id") or None,
        people_airtable_record_id=row.get("people_airtable_record_id") or None,
        last_error=row.get("last_error") or None,
        expires_at=row.get("expires_at") or None,
        created_at=row.get("created_at") or None,
        updated_at=row.get("updated_at") or None,
        opened_at=row.get("opened_at") or None,
        submitted_at=row.get("submitted_at") or None,
    )


def _resolve_expires_at(payload: PeopleRegistryInviteCreatePayload) -> Optional[str]:
    if payload.expires_at:
        return payload.expires_at
    if payload.expires_in_days:
        return (datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)).isoformat()
    return None


def _is_expired(row: Dict[str, Any]) -> bool:
    raw_expires_at = row.get("expires_at")
    if not raw_expires_at:
        return False

    try:
        expires_at = datetime.fromisoformat(str(raw_expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return expires_at < datetime.now(timezone.utc)


def fetch_people_registry_invite_by_token(token: str) -> Optional[Dict[str, Any]]:
    result = (
        supabase.table(PEOPLE_REGISTRY_INVITES_TABLE)
        .select("*")
        .eq("token", token)
        .limit(1)
        .execute()
    )
    return _extract_first_row(result)


def list_people_registry_invites_response(
    *,
    workspace_slug: str,
    status: Optional[str] = None,
    limit: int = 50,
) -> PeopleRegistryInviteListResponsePayload:
    normalized_workspace = _normalized_slug(workspace_slug) or ""
    bounded_limit = max(1, min(int(limit or 50), 200))

    if not normalized_workspace:
        return PeopleRegistryInviteListResponsePayload(ok=True, items=[], total=0)

    try:
        query = (
            supabase.table(PEOPLE_REGISTRY_INVITES_TABLE)
            .select("*")
            .eq("workspace_slug", normalized_workspace)
            .order("created_at", desc=True)
            .limit(bounded_limit)
        )
        if status and status != "all":
            query = query.eq("status", status)
        result = query.execute()
        rows = getattr(result, "data", None)
    except Exception:
        rows = []

    if not isinstance(rows, list):
        rows = []

    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _is_expired(row) and str(row.get("status") or "") not in {"submitted", "expired", "discontinued"}:
            row = {**row, "status": "expired"}
        items.append(_invite_payload_from_row(row))

    return PeopleRegistryInviteListResponsePayload(
        ok=True,
        items=items,
        total=len(items),
    )


def _update_invite(token: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = {**fields, "updated_at": _utc_now_iso()}
    result = (
        supabase.table(PEOPLE_REGISTRY_INVITES_TABLE)
        .update(payload)
        .eq("token", token)
        .execute()
    )
    return _extract_first_row(result)


def create_people_registry_invite_response(
    payload: PeopleRegistryInviteCreatePayload,
) -> PeopleRegistryInviteResponsePayload:
    workspace_slug = _normalized_slug(payload.workspace_slug) or ""
    profile = _normalized_slug(payload.profile) or "atabaque_people_v1"
    airtable_clearance_part_id = _normalized_text(payload.airtable_clearance_part_id)

    if not workspace_slug:
        return PeopleRegistryInviteResponsePayload(
            ok=False,
            status="failed",
            invite=None,
            error=_invite_error(
                code="people_registry_invite_validation_failed",
                message="workspace_slug is required.",
                stage="people_registry_invite_validation",
            ),
        )

    now_iso = _utc_now_iso()
    token = str(uuid4())
    row = {
        "token": token,
        "workspace_slug": workspace_slug,
        "profile": profile,
        "status": "pending",
        "airtable_clearance_part_id": airtable_clearance_part_id or "",
        "context": dict(payload.context or {}),
        "people_registry_record_id": None,
        "people_airtable_record_id": None,
        "last_error": None,
        "expires_at": _resolve_expires_at(payload),
        "created_at": now_iso,
        "updated_at": now_iso,
        "opened_at": None,
        "submitted_at": None,
    }

    try:
        result = supabase.table(PEOPLE_REGISTRY_INVITES_TABLE).insert(row).execute()
        created = _extract_first_row(result) or row
    except Exception as exc:
        return PeopleRegistryInviteResponsePayload(
            ok=False,
            status="failed",
            invite=None,
            error=_invite_error(
                code="people_registry_invite_persistence_failed",
                message=f"Could not create people registry invite: {exc}",
                stage="people_registry_invite_persistence",
            ),
        )

    invite = _invite_payload_from_row(created)
    return PeopleRegistryInviteResponsePayload(
        ok=True,
        status=invite.status,
        invite=invite,
        error=None,
    )


def send_people_registry_invite_email_response(
    token: str,
    payload: PeopleRegistryInviteEmailPayload,
) -> PeopleRegistryInviteEmailResponsePayload:
    try:
        row = fetch_people_registry_invite_by_token(token)
    except Exception as exc:
        return PeopleRegistryInviteEmailResponsePayload(
            ok=False,
            invite=None,
            provider_message_id=None,
            error=_invite_error(
                code="people_registry_invite_fetch_failed",
                message=f"Could not fetch people registry invite: {exc}",
                stage="people_registry_invite_email",
            ),
        )

    if not row:
        return PeopleRegistryInviteEmailResponsePayload(
            ok=False,
            invite=None,
            provider_message_id=None,
            error=_invite_error(
                code="people_registry_invite_not_found",
                message="People registry invite was not found.",
                stage="people_registry_invite_email",
            ),
        )

    invite_workspace_slug = str(row.get("workspace_slug") or "").strip().lower()
    requested_workspace_slug = payload.workspace_slug.strip().lower()
    if not invite_workspace_slug or invite_workspace_slug != requested_workspace_slug:
        return PeopleRegistryInviteEmailResponsePayload(
            ok=False,
            invite=None,
            error=_invite_error(
                code="people_registry_invite_workspace_mismatch",
                message="People registry invite does not belong to this workspace.",
                stage="people_registry_invite_authorization",
            ),
        )

    if str(row.get("status") or "") == "discontinued":
        return PeopleRegistryInviteEmailResponsePayload(
            ok=False,
            invite=_invite_payload_from_row(row),
            provider_message_id=None,
            error=_invite_error(
                code="people_registry_invite_discontinued",
                message="People registry invite is discontinued.",
                stage="people_registry_invite_email",
            ),
        )

    if _is_expired(row):
        row = _update_invite(token, {"status": "expired"}) or row
        return PeopleRegistryInviteEmailResponsePayload(
            ok=False,
            invite=_invite_payload_from_row(row),
            provider_message_id=None,
            error=_invite_error(
                code="people_registry_invite_expired",
                message="People registry invite is expired.",
                stage="people_registry_invite_email",
            ),
        )

    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    to_email = str(payload.to_email or context.get("signing_email") or context.get("email") or "").strip()
    if not to_email:
        return PeopleRegistryInviteEmailResponsePayload(
            ok=False,
            invite=_invite_payload_from_row(row),
            provider_message_id=None,
            error=_invite_error(
                code="people_registry_invite_email_missing_recipient",
                message="Invite recipient email is required.",
                stage="people_registry_invite_email",
            ),
        )

    try:
        result = send_people_registry_invite_email(
            to_email=to_email,
            invite_url=_invite_url(
                workspace_slug=str(row.get("workspace_slug") or ""),
                token=str(row.get("token") or token),
            ),
            recipient_name=payload.recipient_name or context.get("party_name"),
            project_title=context.get("project_title") or context.get("clearance_case_name"),
            track_title=context.get("track_title") or context.get("clearance_item_name"),
            role=context.get("requested_role") or context.get("role"),
            remuneration=context.get("remuneration") or context.get("participation_percent"),
            expires_at=row.get("expires_at"),
            message=payload.message,
            workspace_slug=str(row.get("workspace_slug") or "atabaque"),
        )
    except Exception as exc:
        updated_context = {
            **context,
            "email_last_error": str(exc),
        }
        row = _update_invite(token, {"context": updated_context, "last_error": str(exc)}) or row
        return PeopleRegistryInviteEmailResponsePayload(
            ok=False,
            invite=_invite_payload_from_row(row),
            provider_message_id=None,
            error=_invite_error(
                code="people_registry_invite_email_failed",
                message=f"Could not send people registry invite email: {exc}",
                stage="people_registry_invite_email",
            ),
        )

    updated_context = {
        **context,
        "email_last_sent_at": _utc_now_iso(),
        "email_last_sent_to": to_email,
        "email_last_error": None,
    }
    current_status = str(row.get("status") or "pending")
    update_fields: Dict[str, Any] = {
        "context": updated_context,
        "last_error": None,
    }
    if current_status == "pending":
        update_fields["status"] = "sent"
    row = _update_invite(token, update_fields) or row
    return PeopleRegistryInviteEmailResponsePayload(
        ok=True,
        invite=_invite_payload_from_row(row),
        provider_message_id=result.get("provider_message_id"),
        error=None,
    )


def get_people_registry_invite_response(token: str) -> PeopleRegistryInviteResponsePayload:
    try:
        row = fetch_people_registry_invite_by_token(token)
    except Exception as exc:
        return PeopleRegistryInviteResponsePayload(
            ok=False,
            status="failed",
            invite=None,
            error=_invite_error(
                code="people_registry_invite_fetch_failed",
                message=f"Could not fetch people registry invite: {exc}",
                stage="people_registry_invite_fetch",
            ),
        )

    if not row:
        return PeopleRegistryInviteResponsePayload(
            ok=False,
            status="failed",
            invite=None,
            error=_invite_error(
                code="people_registry_invite_not_found",
                message="People registry invite was not found.",
                stage="people_registry_invite_fetch",
            ),
        )

    if str(row.get("status") or "") == "discontinued":
        invite = _invite_payload_from_row(row)
        return PeopleRegistryInviteResponsePayload(
            ok=False,
            status="discontinued",
            invite=invite,
            error=_invite_error(
                code="people_registry_invite_discontinued",
                message="People registry invite is discontinued.",
                stage="people_registry_invite_fetch",
            ),
        )

    if _is_expired(row):
        row = _update_invite(token, {"status": "expired"}) or row
        invite = _invite_payload_from_row(row)
        return PeopleRegistryInviteResponsePayload(
            ok=False,
            status="expired",
            invite=invite,
            error=_invite_error(
                code="people_registry_invite_expired",
                message="People registry invite is expired.",
                stage="people_registry_invite_fetch",
            ),
        )

    if str(row.get("status") or "") in {"pending", "sent"}:
        row = _update_invite(
            token,
            {
                "status": "opened",
                "opened_at": _utc_now_iso(),
            },
        ) or row

    invite = _invite_payload_from_row(row)
    return PeopleRegistryInviteResponsePayload(
        ok=True,
        status=invite.status,
        invite=invite,
        error=None,
    )


def _merge_invite_context_into_person(
    *,
    invite: Dict[str, Any],
    payload: PeopleRegistryInviteSubmitPayload,
) -> PeopleRegistryPayload:
    person = payload.person
    workspace_slug = str(invite.get("workspace_slug") or person.workspace_slug)
    profile = str(invite.get("profile") or person.profile or "atabaque_people_v1")
    context = invite.get("context") if isinstance(invite.get("context"), dict) else {}
    participation = payload.participation.model_dump(mode="json")

    external_refs = dict(person.additional_info.external_refs or {})
    external_refs["clearance_people_invite"] = {
        "token": invite.get("token"),
        "airtable_clearance_part_id": invite.get("airtable_clearance_part_id"),
        "workspace_slug": workspace_slug,
        "profile": profile,
        "context": context,
        "participation": participation,
    }

    additional_info = person.additional_info.model_copy(
        update={"external_refs": external_refs}
    )
    meta = person.meta.model_copy(
        update={
            "source": person.meta.source or f"sunbeat.{workspace_slug}.people_registry.clearance_invite",
            "submitted_at": person.meta.submitted_at or _utc_now_iso(),
        }
    )
    return person.model_copy(
        update={
            "workspace_slug": workspace_slug,
            "workflow_type": "people_registry",
            "profile": profile,
            "additional_info": additional_info,
            "meta": meta,
        }
    )


def _coerce_airtable_percent(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number < 0:
        return None
    if number > 1:
        number = number / 100
    if number > 1:
        return None
    return number


def _clean_airtable_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        if isinstance(value, list):
            value = [item for item in value if item not in (None, "")]
            if not value:
                continue
        cleaned[key] = value
    return cleaned


def _build_clearance_parte_patch_fields(
    *,
    people_airtable_record_id: str,
    prepared: PeopleRegistryPreparedPayload,
    payload: PeopleRegistryInviteSubmitPayload,
) -> Dict[str, Any]:
    participation = payload.participation
    confirmation_status = str(participation.confirmation_status or "").strip().lower()
    notes = _normalized_text(participation.notes)

    fields: Dict[str, Any] = {
        PARTE_CONTEXT_FIELDS["person_link"]: [people_airtable_record_id],
        PARTE_CONTEXT_FIELDS["registration_status"]: "Completo",
        PARTE_CONTEXT_FIELDS["communication_channel"]: "Formulário Sunbeat",
        PARTE_CONTEXT_FIELDS["document"]: format_document_id_for_display(
            prepared.party.document_id
        ),
        PARTE_CONTEXT_FIELDS["email"]: prepared.contact.email_primary,
        PARTE_CONTEXT_FIELDS["phone"]: prepared.contact.phone_primary,
        PARTE_CONTEXT_FIELDS["musical_role"]: _normalized_text(
            participation.musical_role
        ),
        PARTE_CONTEXT_FIELDS["remuneration_type"]: _normalized_text(
            participation.remuneration_type
        ),
        PARTE_CONTEXT_FIELDS["participation_percent"]: _coerce_airtable_percent(
            participation.participation_percent
        ),
        PARTE_CONTEXT_FIELDS["fixed_amount"]: participation.fixed_amount,
        PARTE_CONTEXT_FIELDS["remuneration_notes"]: notes,
    }

    if confirmation_status in CONTESTED_CONFIRMATION_VALUES:
        fields[PARTE_CONTEXT_FIELDS["approval_status"]] = "Em negociação"

    return _clean_airtable_fields(fields)


def _clearance_partes_table_url(workspace_slug: str) -> str:
    airtable_extra = get_airtable_extra_config(workspace_slug, "rights_clearance")
    base_id = airtable_extra.get("base_id_override") or _base_id()
    table_name = (
        airtable_extra.get("clearance_partes_table_override")
        or CLEARANCE_PARTES_TABLE
    )
    return f"https://api.airtable.com/v0/{base_id}/{quote(table_name, safe='')}"


def _patch_clearance_parte(
    *,
    workspace_slug: str,
    airtable_clearance_part_id: str,
    fields: Dict[str, Any],
) -> Dict[str, Any]:
    url = f"{_clearance_partes_table_url(workspace_slug)}/{airtable_clearance_part_id}"
    return _request_json(
        "PATCH",
        url,
        payload={"fields": fields, "typecast": True},
    )


def _resolve_people_response_for_linking(
    people_response: PeopleRegistryResponsePayload,
) -> PeopleRegistryResponsePayload:
    if people_response.ok or people_response.status != "conflict" or not people_response.record:
        return people_response

    existing_response = get_people_registry_record_response(
        people_response.record.record_id
    )
    return existing_response if existing_response.ok else people_response


def _people_row_for_response(
    people_response: PeopleRegistryResponsePayload,
) -> Optional[Dict[str, Any]]:
    if not people_response.record:
        return None

    try:
        return fetch_people_registry_record_by_id(people_response.record.record_id)
    except Exception:
        return None


def submit_people_registry_invite_response(
    token: str,
    payload: PeopleRegistryInviteSubmitPayload,
) -> PeopleRegistryInviteSubmitResponsePayload:
    try:
        invite_row = fetch_people_registry_invite_by_token(token)
    except Exception as exc:
        return PeopleRegistryInviteSubmitResponsePayload(
            ok=False,
            status="failed",
            invite=None,
            people=None,
            error=_invite_error(
                code="people_registry_invite_fetch_failed",
                message=f"Could not fetch people registry invite: {exc}",
                stage="people_registry_invite_fetch",
            ),
        )

    if not invite_row:
        return PeopleRegistryInviteSubmitResponsePayload(
            ok=False,
            status="failed",
            invite=None,
            people=None,
            error=_invite_error(
                code="people_registry_invite_not_found",
                message="People registry invite was not found.",
                stage="people_registry_invite_fetch",
            ),
        )

    if str(invite_row.get("status") or "") == "discontinued":
        return PeopleRegistryInviteSubmitResponsePayload(
            ok=False,
            status="discontinued",
            invite=_invite_payload_from_row(invite_row),
            people=None,
            error=_invite_error(
                code="people_registry_invite_discontinued",
                message="People registry invite is discontinued.",
                stage="people_registry_invite_submit",
            ),
        )

    if _is_expired(invite_row):
        updated = _update_invite(token, {"status": "expired"}) or invite_row
        return PeopleRegistryInviteSubmitResponsePayload(
            ok=False,
            status="expired",
            invite=_invite_payload_from_row(updated),
            people=None,
            error=_invite_error(
                code="people_registry_invite_expired",
                message="People registry invite is expired.",
                stage="people_registry_invite_submit",
            ),
        )

    merged_person = _merge_invite_context_into_person(
        invite=invite_row,
        payload=payload,
    )
    people_response = _resolve_people_response_for_linking(
        create_people_registry_record_response(merged_person)
    )

    if not people_response.ok:
        updated = _update_invite(
            token,
            {
                "status": "failed",
                "last_error": people_response.error.message
                if people_response.error
                else "People registry submission failed.",
            },
        ) or invite_row
        return PeopleRegistryInviteSubmitResponsePayload(
            ok=False,
            status="failed",
            invite=_invite_payload_from_row(updated),
            people=people_response,
            error=_invite_error(
                code="people_registry_invite_people_submission_failed",
                message="Could not submit people registry record for this invite.",
                stage="people_registry_invite_people_submission",
            ),
        )

    people_row = _people_row_for_response(people_response)
    people_airtable_record_id = str(
        (people_row or {}).get("airtable_record_id") or ""
    ).strip()

    if not people_airtable_record_id:
        updated = _update_invite(
            token,
            {
                "status": "submitted_pending_airtable",
                "people_registry_record_id": people_response.record.record_id
                if people_response.record
                else None,
                "people_airtable_record_id": None,
                "submitted_at": _utc_now_iso(),
                "last_error": "Pessoa salva, mas ainda sem airtable_record_id para vincular ao Clearance.",
            },
        ) or invite_row
        return PeopleRegistryInviteSubmitResponsePayload(
            ok=True,
            status="submitted_pending_airtable",
            invite=_invite_payload_from_row(updated),
            people=people_response,
            error=None,
        )

    airtable_clearance_part_id = str(invite_row.get("airtable_clearance_part_id") or "").strip()
    if not airtable_clearance_part_id:
        updated = _update_invite(
            token,
            {
                "status": "submitted",
                "people_registry_record_id": people_response.record.record_id
                if people_response.record
                else None,
                "people_airtable_record_id": people_airtable_record_id,
                "submitted_at": _utc_now_iso(),
                "last_error": None,
            },
        ) or invite_row
        return PeopleRegistryInviteSubmitResponsePayload(
            ok=True,
            status="submitted",
            invite=_invite_payload_from_row(updated),
            people=people_response,
            error=None,
        )

    prepared = people_response.data or normalize_people_registry_payload(merged_person)
    fields = _build_clearance_parte_patch_fields(
        people_airtable_record_id=people_airtable_record_id,
        prepared=prepared,
        payload=payload,
    )

    try:
        _patch_clearance_parte(
            workspace_slug=str(invite_row.get("workspace_slug") or merged_person.workspace_slug),
            airtable_clearance_part_id=airtable_clearance_part_id,
            fields=fields,
        )
    except Exception as exc:
        updated = _update_invite(
            token,
            {
                "status": "submitted_pending_airtable",
                "people_registry_record_id": people_response.record.record_id
                if people_response.record
                else None,
                "people_airtable_record_id": people_airtable_record_id,
                "submitted_at": _utc_now_iso(),
                "last_error": f"Pessoa salva, mas nao foi possivel vincular ao Clearance: {exc}",
            },
        ) or invite_row
        return PeopleRegistryInviteSubmitResponsePayload(
            ok=True,
            status="submitted_pending_airtable",
            invite=_invite_payload_from_row(updated),
            people=people_response,
            error=None,
        )

    updated = _update_invite(
        token,
        {
            "status": "submitted",
            "people_registry_record_id": people_response.record.record_id
            if people_response.record
            else None,
            "people_airtable_record_id": people_airtable_record_id,
            "submitted_at": _utc_now_iso(),
            "last_error": None,
        },
    ) or invite_row
    return PeopleRegistryInviteSubmitResponsePayload(
        ok=True,
        status="submitted",
        invite=_invite_payload_from_row(updated),
        people=people_response,
        error=None,
    )
