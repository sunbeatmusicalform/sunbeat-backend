from __future__ import annotations

import logging
import os
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.modules.admin_config import router as admin_config_router
from app.modules.ai_gateway import router as ai_gateway_router
from app.modules.drive_config import router as drive_config_router
from app.modules.people_registry import router as people_registry_router
from app.modules.release_intake_history import router as release_intake_history_router
from app.modules.release_drafts import router as drafts_router
from app.modules.submissions import router as submissions_router
from app.modules.tables import router as tables_router
from app.modules.workspaces import router as workspaces_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

app = FastAPI(
    title="Sunbeat API",
    version="1.0.0",
    description="Infrastructure for music release metadata",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://sunbeat.pro",
        "https://www.sunbeat.pro",
        "https://sunbeat.com.br",
        "https://www.sunbeat.com.br",
        "https://sunbeat-frontend.fly.dev",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_config_router)
app.include_router(drafts_router)
app.include_router(drive_config_router)
app.include_router(people_registry_router)
app.include_router(release_intake_history_router)
app.include_router(submissions_router)
app.include_router(tables_router)
app.include_router(workspaces_router)
app.include_router(ai_gateway_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    tb = traceback.format_exc()
    logging.getLogger("sunbeat.errors").error("Unhandled exception: %s\n%s", exc, tb)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "sunbeat-api"}


# ---------------------------------------------------------------------------
# Front novo da Sunbeat (SPA React) servido na mesma origem da API.
# O build do Vite vive em app/static (gerado a partir do repo do front).
# Rotas da API têm precedência por serem registradas antes do catch-all.
# ---------------------------------------------------------------------------
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

if os.path.isdir(STATIC_DIR):
    assets_dir = os.path.join(STATIC_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="static-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        candidate = os.path.normpath(os.path.join(STATIC_DIR, full_path))
        if full_path and candidate.startswith(STATIC_DIR) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
