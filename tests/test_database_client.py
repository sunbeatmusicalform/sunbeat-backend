import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")

from app.core.database import build_supabase_client


def test_database_client_disables_http2_by_default() -> None:
    http_client = MagicMock()

    with (
        patch("app.core.database.httpx.Client", return_value=http_client) as client_factory,
        patch("app.core.database.create_client", return_value=MagicMock()) as create_client,
    ):
        build_supabase_client()

    client_factory.assert_called_once_with(http2=False, timeout=120)
    assert create_client.call_args.kwargs["options"].httpx_client is http_client


def test_database_client_uses_injected_transport() -> None:
    http_client = MagicMock()

    with patch("app.core.database.create_client", return_value=MagicMock()) as create_client:
        build_supabase_client(httpx_client=http_client)

    assert create_client.call_args.kwargs["options"].httpx_client is http_client
