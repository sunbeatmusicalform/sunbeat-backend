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

    status_code = 422 if response.status == "invalid" else 500
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
    )


# Commit 2 keeps people_registry scoped to:
# - POST create only
# - internal persistence only
# - no dedupe
# - no Airtable sync
