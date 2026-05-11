from __future__ import annotations

import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.modules.admin_config import router as admin_config_router
from app.modules.ai_gateway import router as ai_gateway_router
from app.modules.people_registry import router as people_registry_router
from app.modules.release_drafts import router as drafts_router
from app.modules.submissions import router as submissions_router
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
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_config_router)
app.include_router(drafts_router)
app.include_router(people_registry_router)
app.include_router(submissions_router)
app.include_router(workspaces_router)
app.include_router(ai_gateway_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    tb = traceback.format_exc()
    logging.getLogger("sunbeat.errors").error("Unhandled exception: %s\n%s", exc, tb)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "traceback": tb},
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "sunbeat-api"}
