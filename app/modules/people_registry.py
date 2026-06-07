from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.schemas.people_registry import (
    PeopleRegistryLookupResponsePayload,
    PeopleRegistryPayload,
    PeopleRegistryResponsePayload,
)
from app.services.people_registry import (
    build_people_registry_response,
    create_people_registry_record_response,
    get_people_registry_record_response,
    get_people_registry_record_by_edit_token_response,
    lookup_people_registry_records,
    update_people_registry_record_response,
)

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
