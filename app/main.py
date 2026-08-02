from __future__ import annotations

import json
import logging
import os
import re
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.database import supabase
from app.modules.admin_config import router as admin_config_router
from app.modules.ai_gateway import router as ai_gateway_router
from app.modules.drive_config import router as drive_config_router
from app.modules.email_config import router as email_config_router
from app.modules.file_uploads import router as file_uploads_router
from app.modules.form_config import router as form_config_router
from app.modules.edit_access import router as edit_access_router
from app.modules.help_config import router as help_config_router
from app.modules.lyrics_alignment import router as lyrics_alignment_router
from app.modules.people_registry import router as people_registry_router
from app.modules.portal_branding import router as portal_branding_router
from app.modules.portal_operations import router as portal_operations_router
from app.modules.portal_session import router as portal_session_router
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
app.include_router(email_config_router)
app.include_router(file_uploads_router)
app.include_router(form_config_router)
app.include_router(edit_access_router)
app.include_router(help_config_router)
app.include_router(lyrics_alignment_router)
app.include_router(people_registry_router)
app.include_router(portal_branding_router)
app.include_router(portal_operations_router)
app.include_router(portal_session_router)
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

# Cache do index.html em memória para não ler do disco a cada request
_index_html_template: str | None = None


def _get_index_html() -> str:
    global _index_html_template
    if _index_html_template is None:
        path = os.path.join(STATIC_DIR, "index.html")
        with open(path, "r", encoding="utf-8") as f:
            _index_html_template = f.read()
    return _index_html_template


def _inject_og_tags(html: str, branding: dict, request_url: str) -> str:
    """Substitui meta tags OG genéricas pelas do workspace e injeta branding inicial no HTML."""
    title = branding.get("social_title") or branding.get("form_title") or branding.get("workspace_name") or "Sunbeat"
    description = branding.get("social_description") or branding.get("slogan") or branding.get("intro_text") or "Formulário de intake musical"
    image = branding.get("social_image_url") or branding.get("badge_url") or branding.get("logo_url") or ""
    theme_color = branding.get("primary_color") or "#000e14"

    # Se a image for path relativo, converte para absoluto
    if image and image.startswith("/"):
        image = f"https://sunbeat.pro{image}"

    # Substituições via regex
    html = re.sub(
        r'<title>.*?</title>',
        f'<title>{title}</title>',
        html,
        count=1,
    )
    html = re.sub(
        r'<meta name="description" content=".*?"\s*/?>',
        f'<meta name="description" content="{description}" />',
        html,
        count=1,
    )
    html = re.sub(
        r'<meta property="og:title" content=".*?"\s*/?>',
        f'<meta property="og:title" content="{title}" />',
        html,
        count=1,
    )
    html = re.sub(
        r'<meta property="og:description" content=".*?"\s*/?>',
        f'<meta property="og:description" content="{description}" />',
        html,
        count=1,
    )
    if image:
        html = re.sub(
            r'<meta property="og:image" content=".*?"\s*/?>',
            f'<meta property="og:image" content="{image}" />',
            html,
            count=1,
        )
    html = re.sub(
        r'<meta name="theme-color" content=".*?"\s*/?>',
        f'<meta name="theme-color" content="{theme_color}" />',
        html,
        count=1,
    )

    # Injeta branding completo como dados iniciais para o frontend consumir
    # sem precisar de chamada API adicional
    branding_json = json.dumps(branding, ensure_ascii=False, separators=(",", ":"))
    branding_script = f'<script>window.__INITIAL_BRANDING__={branding_json}</script>'
    html = html.replace('</head>', f'{branding_script}</head>')

    return html


async def _fetch_workspace_branding(workspace_slug: str) -> dict | None:
    try:
        res = (
            supabase.table("workspace_branding")
            .select("*")
            .eq("workspace_slug", workspace_slug)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    except Exception as exc:
        logging.getLogger("sunbeat.og").warning("Failed to fetch branding for %s: %s", workspace_slug, exc)
    return None


if os.path.isdir(STATIC_DIR):
    assets_dir = os.path.join(STATIC_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="static-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str, request: Request):
        candidate = os.path.normpath(os.path.join(STATIC_DIR, full_path))
        if full_path and candidate.startswith(STATIC_DIR) and os.path.isfile(candidate):
            return FileResponse(candidate)

        # -------------------------------------------------------------------
        # SSR dinâmico de meta tags OG para rotas de intake/workspace
        # -------------------------------------------------------------------
        workspace_slug: str | None = None

        # Detecta /intake/:workspace_slug ou /intake/:workspace_slug/*
        intake_match = re.match(r"^(?:intake|clearance|people|company)/([^/]+)", full_path)
        if intake_match:
            workspace_slug = intake_match.group(1)

        # Também detecta /:workspace_slug (portal ou outras rotas públicas)
        # Mas evita capturar paths de API/assets que já foram servidos acima
        if workspace_slug is None and full_path and "/" not in full_path:
            workspace_slug = full_path

        if workspace_slug:
            branding = await _fetch_workspace_branding(workspace_slug)
            if branding:
                html = _get_index_html()
                html = _inject_og_tags(html, branding, str(request.url))
                return HTMLResponse(content=html, status_code=200)

        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
