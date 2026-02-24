from supabase import create_client
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


supabase = create_client(settings.SUPABASE_URL, _get_supabase_key())