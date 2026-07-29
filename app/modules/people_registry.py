from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.core.admin_auth import require_internal_admin_token
from app.schemas.people_registry import (
    PeopleRegistryInviteCreatePayload,
    PeopleRegistryInviteEmailPayload,
    PeopleRegistryInviteEmailResponsePayload,
    PeopleRegistryInviteListResponsePayload,
    PeopleRegistryInviteResponsePayload,
    PeopleRegistryInviteSubmitPayload,
    PeopleRegistryInviteSubmitResponsePayload,
    PeopleRegistryLookupResponsePayload,
    PeopleRegistryPayload,
    PeopleRegistryResponsePayload,
    PeopleRegistryVerifyResponsePayload,
)
from app.services.people_registry_invites import (
    create_people_registry_invite_response,
    get_people_registry_invite_response,
    list_people_registry_invites_response,
    send_people_registry_invite_email_response,
    submit_people_registry_invite_response,
)
from app.services.people_registry import (
    build_people_registry_response,
    create_people_registry_record_response,
    get_people_registry_record_response,
    get_people_registry_record_by_edit_token_response,
    lookup_people_registry_records,
    update_people_registry_record_response,
)
from app.services.people_registry_verify import verify_people_registry_records

router = APIRouter(prefix="/people-registry", tags=["people_registry"])


def preview_people_registry_payload(
    payload: PeopleRegistryPayload,
) -> PeopleRegistryResponsePayload:
    return build_people_registry_response(payload)


@router.post("/records", response_model=PeopleRegistryResponsePayload, status_code=201)
def create_people_registry_record(
    payload: PeopleRegistryPayload,
):
    response = create_people_registry_record_response(payload)

    if response.ok:
        return response

    if response.status == "conflict":
        status_code = 409
    elif response.status == "invalid":
        status_code = 422
    else:
        status_code = 500

    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
    )


@router.post("/invites", response_model=PeopleRegistryInviteResponsePayload, status_code=201)
def create_people_registry_invite(
    payload: PeopleRegistryInviteCreatePayload,
    _: None = Depends(require_internal_admin_token),
):
    response = create_people_registry_invite_response(payload)

    if response.ok:
        return response

    return JSONResponse(
        status_code=422 if response.error and "validation" in response.error.code else 500,
        content=response.model_dump(mode="json"),
    )


@router.get("/invites", response_model=PeopleRegistryInviteListResponsePayload)
def list_people_registry_invites(
    workspace_slug: str = Query(..., min_length=1),
    status: str | None = Query(default=None),
    limit: int | None = Query(default=50),
    _: None = Depends(require_internal_admin_token),
):
    return list_people_registry_invites_response(
        workspace_slug=workspace_slug,
        status=status,
        limit=limit or 50,
    )


@router.get("/invites/{token}", response_model=PeopleRegistryInviteResponsePayload)
def get_people_registry_invite(token: str):
    response = get_people_registry_invite_response(token)

    if response.ok:
        return response

    status_code = (
        404
        if response.error and response.error.code == "people_registry_invite_not_found"
        else 410
        if response.status in {"expired", "discontinued"}
        else 500
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
    )


@router.post(
    "/invites/{token}/email",
    response_model=PeopleRegistryInviteEmailResponsePayload,
)
def send_people_registry_invite_email(
    token: str,
    payload: PeopleRegistryInviteEmailPayload,
    _: None = Depends(require_internal_admin_token),
):
    response = send_people_registry_invite_email_response(token, payload)

    if response.ok:
        return response

    status_code = (
        404
        if response.error and response.error.code == "people_registry_invite_not_found"
        else 410
        if response.error
        and response.error.code
        in {"people_registry_invite_expired", "people_registry_invite_discontinued"}
        else 422
        if response.error
        and response.error.code
        in {
            "people_registry_invite_email_missing_recipient",
            "people_registry_invite_workspace_mismatch",
        }
        else 500
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
    )


@router.post(
    "/invites/{token}/records",
    response_model=PeopleRegistryInviteSubmitResponsePayload,
    status_code=201,
)
def submit_people_registry_invite(token: str, payload: PeopleRegistryInviteSubmitPayload):
    response = submit_people_registry_invite_response(token, payload)

    if response.ok:
        return response

    status_code = (
        404
        if response.error and response.error.code == "people_registry_invite_not_found"
        else 410
        if response.status in {"expired", "discontinued"}
        else 422
        if response.error
        and response.error.code == "people_registry_invite_people_submission_failed"
        else 500
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
    )


@router.get("/lookup", response_model=PeopleRegistryLookupResponsePayload)
def lookup_people_registry(
    workspace_slug: str = Query(..., min_length=1),
    query: str = Query("", min_length=0),
    roles: str | None = Query(default=None),
    limit: int | None = Query(default=None),
):
    return lookup_people_registry_records(
        workspace_slug=workspace_slug,
        query=query,
        roles=roles,
        limit=limit,
    )


@router.get("/verify", response_model=PeopleRegistryVerifyResponsePayload)
def verify_people_registry(
    workspace_slug: str = Query(..., min_length=1),
    query: str = Query(..., min_length=1),
):
    return verify_people_registry_records(
        workspace_slug=workspace_slug,
        query=query,
    )


@router.get("/records/{record_id}", response_model=PeopleRegistryResponsePayload)
def get_people_registry_record(record_id: str):
    response = get_people_registry_record_response(record_id)

    if response.ok:
        return response

    status_code = (
        404
        if response.error
        and response.error.code == "people_registry_record_not_found"
        else 500
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
    )


@router.get("/records/edit/{edit_token}", response_model=PeopleRegistryResponsePayload)
def get_people_registry_record_by_edit_token(edit_token: str):
    response = get_people_registry_record_by_edit_token_response(edit_token)

    if response.ok:
        return response

    status_code = (
        404
        if response.error
        and response.error.code == "people_registry_record_not_found"
        else 500
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
    )


@router.patch("/records/edit/{edit_token}", response_model=PeopleRegistryResponsePayload)
def patch_people_registry_record(edit_token: str, payload: PeopleRegistryPayload):
    response = update_people_registry_record_response(edit_token, payload)

    if response.ok:
        return response

    status_code = (
        404
        if response.error
        and response.error.code == "people_registry_record_not_found"
        else 422
        if response.status == "invalid"
        else 500
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
    )
