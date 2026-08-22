from __future__ import annotations

import json
import logging
import os
import re
import traceback
import uuid
from html import escape

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import supabase
from app.modules.admin_config import router as admin_config_router
from app.modules.automation import router as automation_router
from app.modules.ai_gateway import router as ai_gateway_router
from app.modules.drive_config import router as drive_config_router
from app.modules.email_config import router as email_config_router
from app.modules.file_uploads import router as file_uploads_router
from app.modules.form_config import router as form_config_router
from app.modules.edit_access import router as edit_access_router
from app.modules.help_config import router as help_config_router
from app.modules.lyrics_alignment import router as lyrics_alignment_router
from app.modules.onboarding import router as onboarding_router
from app.modules.people_registry import router as people_registry_router
from app.modules.portal_branding import router as portal_branding_router
from app.modules.portal_operations import router as portal_operations_router
from app.modules.portal_session import router as portal_session_router
from app.modules.public_leads import router as public_leads_router
from app.modules.release_intake_history import router as release_intake_history_router
from app.modules.release_drafts import router as drafts_router
from app.modules.self_service_auth import router as self_service_auth_router
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


def _trusted_hosts(additional_hosts: str = "") -> list[str]:
    hosts = [
        "sunbeat.pro",
        "*.sunbeat.pro",
        "sunbeat.com.br",
        "*.sunbeat.com.br",
        "sunbeat-backend.fly.dev",
        "localhost",
        "127.0.0.1",
        "testserver",
    ]
    for value in additional_hosts.split(","):
        host = value.strip().lower()
        if host and host not in hosts and re.fullmatch(r"(?:\*\.)?[a-z0-9.-]+", host):
            hosts.append(host)
    return hosts


app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=_trusted_hosts(settings.ADDITIONAL_ALLOWED_HOSTS),
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
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-Admin-Token",
        "X-Portal-Token",
    ],
)


@app.middleware("http")
async def security_and_observability_headers(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://use.typekit.net; "
        "img-src 'self' data: blob: https:; media-src 'self' blob: https:; "
        "font-src 'self' data: https://use.typekit.net https://p.typekit.net; "
        "connect-src 'self' https://sunbeat-backend.fly.dev https://*.supabase.co"
    )
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if request.url.scheme == "https" or forwarded_proto == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith(("/auth", "/workspaces")):
        response.headers["Cache-Control"] = "no-store"
    if response.status_code >= 500:
        logging.getLogger("sunbeat.errors").error(
            "http_5xx request_id=%s method=%s path=%s status=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
        )
    return response

app.include_router(admin_config_router)
app.include_router(automation_router)
app.include_router(drafts_router)
app.include_router(drive_config_router)
app.include_router(email_config_router)
app.include_router(file_uploads_router)
app.include_router(form_config_router)
app.include_router(edit_access_router)
app.include_router(help_config_router)
app.include_router(lyrics_alignment_router)
app.include_router(onboarding_router)
app.include_router(people_registry_router)
app.include_router(portal_branding_router)
app.include_router(portal_operations_router)
app.include_router(portal_session_router)
app.include_router(public_leads_router)
app.include_router(release_intake_history_router)
app.include_router(self_service_auth_router)
app.include_router(submissions_router)
app.include_router(tables_router)
app.include_router(workspaces_router)
app.include_router(ai_gateway_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    tb = traceback.format_exc()
    request_id = getattr(request.state, "request_id", "unknown")
    logging.getLogger("sunbeat.errors").error(
        "Unhandled exception request_id=%s method=%s path=%s: %s\n%s",
        request_id,
        request.method,
        request.url.path,
        exc,
        tb,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
        headers={"X-Request-ID": request_id},
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "sunbeat-api"}


@app.get("/readiness")
def readiness():
    if settings.SELF_SERVICE_SIGNUP_ENABLED and not settings.SUPABASE_SERVICE_ROLE_KEY:
        logging.getLogger("sunbeat.readiness").error(
            "self-service readiness failed: explicit service-role key is missing"
        )
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "service": "sunbeat-api",
                "configuration": "unavailable",
            },
        )
    try:
        supabase.table("workspaces").select("slug").limit(1).execute()
        if settings.SELF_SERVICE_SIGNUP_ENABLED:
            # Read-only schema gate: the release must not become ready until the
            # reviewed security/retention migrations have been applied.
            for table in (
                "self_service_magic_links",
                "portal_sessions",
                "public_rate_limits",
                "public_leads",
                "asset_retention_records",
            ):
                supabase.table(table).select("*").limit(1).execute()
    except Exception as exc:
        logging.getLogger("sunbeat.readiness").error("database readiness failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "service": "sunbeat-api", "database": "unavailable"},
        )
    return {"status": "ready", "service": "sunbeat-api", "database": "reachable"}


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


ACADEMY_ARTICLE_PATH = "/academy/music-release-intake-checklist"


def _inject_marketing_locale(html: str, hostname: str, path: str = "/") -> str:
    """Serve localized, indexable metadata and structured data for public pages."""
    is_brazil = hostname.lower().removeprefix("www.").endswith("sunbeat.com.br")
    origin = "https://sunbeat.com.br" if is_brazil else "https://sunbeat.pro"
    normalized_path = path if path.startswith("/") else f"/{path}"

    if is_brazil:
        lang = "pt-BR"
        if normalized_path in {"/terms", "/termos"}:
            title = "Termos de Uso | Sunbeat"
            description = "Termos que regulam o acesso e o uso da plataforma Sunbeat."
            page_type = "website"
        elif normalized_path in {"/privacy", "/privacidade"}:
            title = "Política de Privacidade | Sunbeat"
            description = "Como a Sunbeat trata dados pessoais, protege informações e atende direitos previstos na LGPD."
            page_type = "website"
        elif normalized_path == "/academy":
            title = "Sunbeat Academy | Operações para lançamentos musicais"
            description = "Guias práticos para labels, managers e equipes criativas criarem fluxos melhores de lançamento, metadados confiáveis e intake de arquivos."
            page_type = "website"
        elif normalized_path == ACADEMY_ARTICLE_PATH:
            title = "Checklist de intake para lançamentos | Sunbeat Academy"
            description = "Um checklist prático para coletar metadados e validar áudio e capa antes que os prazos de distribuição virem emergências."
            page_type = "article"
        else:
            title = "Sunbeat | Intake inteligente para operações criativas"
            description = "A Sunbeat conecta formulários inteligentes, auditoria de arquivos, direitos e integrações para equipes criativas."
            page_type = "website"
    else:
        lang = "en"
        if normalized_path in {"/terms", "/termos"}:
            title = "Terms of Use | Sunbeat"
            description = "Terms governing access to and use of the Sunbeat platform."
            page_type = "website"
        elif normalized_path in {"/privacy", "/privacidade"}:
            title = "Privacy Policy | Sunbeat"
            description = "How Sunbeat processes personal data, protects information, and supports privacy rights."
            page_type = "website"
        elif normalized_path == "/academy":
            title = "Sunbeat Academy | Music release operations"
            description = "Practical guides for labels, managers and creative teams building clearer music release workflows, better metadata and reliable file intake."
            page_type = "website"
        elif normalized_path == ACADEMY_ARTICLE_PATH:
            title = "Music release intake checklist | Sunbeat Academy"
            description = "A practical checklist for collecting release metadata and validating audio and artwork before distribution deadlines become emergencies."
            page_type = "article"
        else:
            title = "Sunbeat | Intelligent intake for creative operations"
            description = "Sunbeat connects intelligent intake forms, file auditing, rights and integrations for creative teams."
            page_type = "website"

    canonical = f"{origin}{normalized_path}"
    image = f"{origin}/brand/og-image.png"

    html = re.sub(r'<html\s+lang="[^"]*"', f'<html lang="{lang}"', html, count=1)
    html = re.sub(r'<title>.*?</title>', f'<title>{escape(title)}</title>', html, count=1)
    html = re.sub(
        r'<meta name="description" content=".*?"\s*/?>',
        f'<meta name="description" content="{escape(description)}" />',
        html,
        count=1,
    )
    html = re.sub(
        r'<meta property="og:title" content=".*?"\s*/?>',
        f'<meta property="og:title" content="{escape(title)}" />',
        html,
        count=1,
    )
    html = re.sub(
        r'<meta property="og:description" content=".*?"\s*/?>',
        f'<meta property="og:description" content="{escape(description)}" />',
        html,
        count=1,
    )
    html = re.sub(
        r'<meta property="og:image" content=".*?"\s*/?>',
        f'<meta property="og:image" content="{image}" />',
        html,
        count=1,
    )
    html = re.sub(
        r'<meta property="og:type" content=".*?"\s*/?>',
        f'<meta property="og:type" content="{page_type}" />',
        html,
        count=1,
    )

    if normalized_path == ACADEMY_ARTICLE_PATH:
        article_headline = (
            "Checklist de intake para lançamentos musicais: metadados, áudio e capa"
            if is_brazil
            else "The music release intake checklist: metadata, audio and artwork"
        )
        structured_data = {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": article_headline,
            "description": description,
            "datePublished": "2026-08-07",
            "dateModified": "2026-08-07",
            "inLanguage": lang,
            "mainEntityOfPage": canonical,
            "image": image,
            "author": {"@type": "Organization", "name": "Sunbeat"},
            "publisher": {"@type": "Organization", "name": "Sunbeat", "url": origin},
        }
    elif normalized_path == "/academy":
        structured_data = {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "Sunbeat Academy",
            "description": description,
            "url": canonical,
            "inLanguage": lang,
            "isPartOf": {"@type": "WebSite", "name": "Sunbeat", "url": origin},
        }
    else:
        structured_data = {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "Organization", "@id": f"{origin}/#organization", "name": "Sunbeat", "url": origin, "logo": f"{origin}/brand/icon-512.png"},
                {"@type": "WebSite", "@id": f"{origin}/#website", "name": "Sunbeat", "url": origin, "inLanguage": lang, "publisher": {"@id": f"{origin}/#organization"}},
                {"@type": "SoftwareApplication", "name": "Sunbeat", "applicationCategory": "BusinessApplication", "operatingSystem": "Web", "url": origin, "description": description, "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}},
            ],
        }

    alternate_en = f"https://sunbeat.pro{normalized_path}"
    alternate_pt = f"https://sunbeat.com.br{normalized_path}"
    alternates = (
        f'<link rel="canonical" href="{canonical}" />'
        f'<link rel="alternate" hreflang="en" href="{alternate_en}" />'
        f'<link rel="alternate" hreflang="pt-BR" href="{alternate_pt}" />'
        f'<link rel="alternate" hreflang="x-default" href="{alternate_en}" />'
        f'<link rel="alternate" type="application/rss+xml" title="Sunbeat Academy" href="{origin}/feed.xml" />'
        '<meta name="robots" content="index,follow,max-image-preview:large" />'
        '<meta property="og:site_name" content="Sunbeat" />'
        f'<meta property="og:url" content="{canonical}" />'
        f'<meta property="og:locale" content="{"pt_BR" if is_brazil else "en_US"}" />'
        '<meta name="twitter:card" content="summary_large_image" />'
        f'<meta name="twitter:title" content="{escape(title)}" />'
        f'<meta name="twitter:description" content="{escape(description)}" />'
        f'<meta name="twitter:image" content="{image}" />'
        f'<script type="application/ld+json">{json.dumps(structured_data, ensure_ascii=False, separators=(",", ":"))}</script>'
    )
    return html.replace("</head>", f"{alternates}</head>")


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


def _public_origin(hostname: str) -> tuple[str, str]:
    is_brazil = hostname.lower().removeprefix("www.").endswith("sunbeat.com.br")
    return ("https://sunbeat.com.br", "pt-BR") if is_brazil else ("https://sunbeat.pro", "en")


def _robots_txt(hostname: str) -> str:
    origin, _ = _public_origin(hostname)
    return f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {origin}/sitemap.xml\n"


def _sitemap_xml(hostname: str) -> str:
    origin, _ = _public_origin(hostname)
    paths = ("/", "/academy", ACADEMY_ARTICLE_PATH)
    entries = []
    for path in paths:
        en_url = f"https://sunbeat.pro{path}"
        pt_url = f"https://sunbeat.com.br{path}"
        entries.append(
            "<url>"
            f"<loc>{origin}{path}</loc>"
            "<lastmod>2026-08-07</lastmod>"
            f'<xhtml:link rel="alternate" hreflang="en" href="{en_url}" />'
            f'<xhtml:link rel="alternate" hreflang="pt-BR" href="{pt_url}" />'
            f'<xhtml:link rel="alternate" hreflang="x-default" href="{en_url}" />'
            "</url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">'
        f"{''.join(entries)}"
        "</urlset>"
    )


def _academy_feed_xml(hostname: str) -> str:
    origin, lang = _public_origin(hostname)
    is_brazil = lang == "pt-BR"
    title = "Checklist de intake para lançamentos musicais: metadados, áudio e capa" if is_brazil else "The music release intake checklist: metadata, audio and artwork"
    description = "Um checklist prático para organizar metadados, áudio e capa." if is_brazil else "A practical checklist for organizing metadata, audio and artwork."
    article_url = f"{origin}{ACADEMY_ARTICLE_PATH}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rss version="2.0"><channel><title>Sunbeat Academy</title><link>{origin}/academy</link>'
        f"<description>{escape(description)}</description><language>{lang}</language>"
        f"<item><title>{escape(title)}</title><link>{article_url}</link><guid>{article_url}</guid>"
        f"<pubDate>Fri, 07 Aug 2026 12:00:00 GMT</pubDate><description>{escape(description)}</description></item>"
        "</channel></rss>"
    )


if os.path.isdir(STATIC_DIR):
    html_no_cache_headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    operational_noindex_headers = {
        **html_no_cache_headers,
        "X-Robots-Tag": "noindex, nofollow, noarchive",
    }

    assets_dir = os.path.join(STATIC_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="static-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str, request: Request):
        hostname = request.url.hostname or "sunbeat.pro"
        if full_path == "robots.txt":
            return Response(content=_robots_txt(hostname), media_type="text/plain")
        if full_path == "sitemap.xml":
            return Response(content=_sitemap_xml(hostname), media_type="application/xml")
        if full_path == "feed.xml":
            return Response(content=_academy_feed_xml(hostname), media_type="application/rss+xml")

        marketing_paths = {
            "",
            "academy",
            ACADEMY_ARTICLE_PATH.removeprefix("/"),
            "terms",
            "termos",
            "privacy",
            "privacidade",
        }
        if full_path in marketing_paths:
            marketing_path = "/" if full_path == "" else f"/{full_path}"
            html = _inject_marketing_locale(
                _get_index_html(),
                hostname,
                marketing_path,
            )
            return HTMLResponse(
                content=html,
                status_code=200,
                headers=html_no_cache_headers,
            )

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
                return HTMLResponse(
                    content=html,
                    status_code=200,
                    headers=operational_noindex_headers,
                )

        if full_path == "concept":
            html = _inject_marketing_locale(_get_index_html(), hostname, "/")
            return HTMLResponse(content=html, status_code=200, headers=operational_noindex_headers)

        return FileResponse(
            os.path.join(STATIC_DIR, "index.html"),
            headers=operational_noindex_headers,
        )
