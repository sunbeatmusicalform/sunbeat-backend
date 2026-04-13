from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.schemas.people_registry import (
    PeopleRegistryPayload,
    PeopleRegistryResponsePayload,
)
from app.services.people_registry import (
    build_people_registry_response,
    create_people_registry_record_response,
    get_people_registry_record_response,
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


# Commit 3 keeps people_registry scoped to:
# - POST create only
# - GET by record_id
# - deterministic dedupe only
# - no Airtable sync
