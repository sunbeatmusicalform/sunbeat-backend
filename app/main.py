# app/main.py
import os
import time
import uuid
from typing import List

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.modules.submissions import router as submissions_router


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_csv(name: str) -> List[str]:
    v = os.getenv(name, "").strip()
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


APP_ENV = os.getenv("APP_ENV", "prod").lower()
IS_PROD = APP_ENV == "prod"
ENABLE_DOCS = _env_bool("ENABLE_DOCS", default=not IS_PROD)

ALLOWED_ORIGINS = _split_csv("ALLOWED_ORIGINS")  # ex: https://sunbeat.pro,https://www.sunbeat.pro
ALLOWED_HOSTS = _split_csv("ALLOWED_HOSTS") or ["localhost", "127.0.0.1", "*.fly.dev"]

# Rate limit simples (por IP) - sem Redis (MVP)
RATE_LIMIT_ON = _env_bool("RATE_LIMIT_ON", default=True)
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "120"))  # 120 req/min por IP
_BUCKET = {}  # {ip: (window_start_ts, count)}


app = FastAPI(
    title="Sunbeat Backend",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)

# Gzip
app.add_middleware(GZipMiddleware, minimum_size=800)

# Trusted hosts
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

# CORS estrito (só habilita se você configurar ALLOWED_ORIGINS)
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["x-request-id", "x-response-time-ms"],
    )


@app.middleware("http")
async def security_headers_and_request_id(request: Request, call_next):
    # request id
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())

    # rate limit simples por IP (MVP)
    if RATE_LIMIT_ON:
        ip = request.headers.get("fly-client-ip") or request.client.host or "unknown"
        now = time.time()
        window = int(now // 60)  # janela de 60s
        key = f"{ip}:{window}"
        count = _BUCKET.get(key, 0) + 1
        _BUCKET[key] = count
        if count > RATE_LIMIT_RPM:
            raise HTTPException(status_code=429, detail="Too many requests")

    t0 = time.perf_counter()
    response = await call_next(request)
    dt_ms = int((time.perf_counter() - t0) * 1000)

    # headers de segurança básicos
    response.headers["x-request-id"] = rid
    response.headers["x-response-time-ms"] = str(dt_ms)
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["x-frame-options"] = "DENY"
    response.headers["referrer-policy"] = "no-referrer"
    # HSTS só se HTTPS (Fly geralmente é)
    response.headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"

    return response


# Rotas fixas (evitam cair em rotas dinâmicas)
@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True, "env": APP_ENV}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/robots.txt", include_in_schema=False)
def robots():
    # evita indexação acidental
    return Response(content="User-agent: *\nDisallow: /\n", media_type="text/plain")


# Routers SEMPRE com prefixo
app.include_router(submissions_router, prefix="/submissions", tags=["submissions"])