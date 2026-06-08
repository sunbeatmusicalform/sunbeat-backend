from __future__ import annotations

from fastapi import APIRouter, Query

from app.schemas.release_intake_history import (
    ReleaseIntakeHistoryLookupResponsePayload,
)
from app.services.release_intake_history import (
    lookup_release_intake_submitter_history,
)

router = APIRouter(prefix="/release-intake", tags=["release_intake"])


@router.get(
    "/history-lookup",
    response_model=ReleaseIntakeHistoryLookupResponsePayload,
)
def lookup_release_intake_history(
    workspace_slug: str = Query(..., min_length=1),
    field: str = Query(..., min_length=1),
    query: str = Query("", min_length=0),
    limit: int | None = Query(default=None),
    draft_token: str | None = Query(default=None),
    edit_token: str | None = Query(default=None),
):
    return lookup_release_intake_submitter_history(
        workspace_slug=workspace_slug,
        field=field,
        query=query,
        limit=limit,
        draft_token=draft_token,
        edit_token=edit_token,
    )
