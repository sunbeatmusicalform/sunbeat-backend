from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.core.admin_auth import require_admin_token
from app.services.tables_gantt import build_gantt_response

router = APIRouter(prefix="/tables", tags=["tables"])


@router.get("/{workspace_slug}/gantt")
def get_tables_gantt(
    workspace_slug: str,
    limit: int = Query(default=200, ge=1, le=500),
    _: None = Depends(require_admin_token),
):
    try:
        return build_gantt_response(workspace_slug=workspace_slug, max_records=limit)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "workspace_slug": workspace_slug,
                "error": {
                    "message": str(exc),
                    "stage": "tables_gantt",
                },
            },
        )
