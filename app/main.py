from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(drafts_router)
app.include_router(submissions_router)
app.include_router(workspaces_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "sunbeat-api"}