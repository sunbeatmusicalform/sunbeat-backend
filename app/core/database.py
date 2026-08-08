import httpx
from supabase import ClientOptions, create_client

from app.core.config import settings


def _get_supabase_key() -> str:
    # Prioridade: service role (backend)
    if settings.SUPABASE_SERVICE_ROLE_KEY:
        return settings.SUPABASE_SERVICE_ROLE_KEY

    # Fallback: anon
    if settings.SUPABASE_ANON_KEY:
        return settings.SUPABASE_ANON_KEY

    # Fallback legado
    if settings.SUPABASE_KEY:
        return settings.SUPABASE_KEY

    raise RuntimeError(
        "No Supabase key found. Set SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY (or SUPABASE_KEY legacy) in .env"
    )


def build_supabase_client(*, httpx_client: httpx.Client | None = None):
    """Build the shared client on HTTP/1.1 to avoid terminated HTTP/2 streams."""
    client = httpx_client if httpx_client is not None else httpx.Client(http2=False, timeout=120)
    return create_client(
        settings.SUPABASE_URL,
        _get_supabase_key(),
        options=ClientOptions(httpx_client=client),
    )


supabase = build_supabase_client()
