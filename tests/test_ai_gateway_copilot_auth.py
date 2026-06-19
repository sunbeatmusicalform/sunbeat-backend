from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")

try:
    import supabase  # noqa: F401
except ModuleNotFoundError:
    supabase_stub = types.ModuleType("supabase")
    supabase_stub.create_client = lambda *_args, **_kwargs: object()
    sys.modules["supabase"] = supabase_stub

from app.modules import ai_gateway


class SetupCopilotAuthTests(unittest.TestCase):
    def test_allows_missing_secret_when_no_server_secret_is_configured(self) -> None:
        with (
            patch.object(ai_gateway.settings, "AI_COPILOT_SECRET", None),
            patch.object(ai_gateway.settings, "INTERNAL_ADMIN_TOKEN", None),
        ):
            self.assertTrue(ai_gateway._copilot_secret_is_valid(None))

    def test_accepts_dedicated_copilot_secret(self) -> None:
        with (
            patch.object(ai_gateway.settings, "AI_COPILOT_SECRET", "copilot-secret"),
            patch.object(ai_gateway.settings, "INTERNAL_ADMIN_TOKEN", None),
        ):
            self.assertTrue(ai_gateway._copilot_secret_is_valid("copilot-secret"))

    def test_accepts_internal_admin_token(self) -> None:
        with (
            patch.object(ai_gateway.settings, "AI_COPILOT_SECRET", "copilot-secret"),
            patch.object(ai_gateway.settings, "INTERNAL_ADMIN_TOKEN", "internal-token"),
        ):
            self.assertTrue(ai_gateway._copilot_secret_is_valid("internal-token"))

    def test_rejects_missing_or_wrong_secret_when_server_secret_is_configured(self) -> None:
        with (
            patch.object(ai_gateway.settings, "AI_COPILOT_SECRET", "copilot-secret"),
            patch.object(ai_gateway.settings, "INTERNAL_ADMIN_TOKEN", "internal-token"),
        ):
            self.assertFalse(ai_gateway._copilot_secret_is_valid(None))
            self.assertFalse(ai_gateway._copilot_secret_is_valid("wrong-secret"))


if __name__ == "__main__":
    unittest.main()
