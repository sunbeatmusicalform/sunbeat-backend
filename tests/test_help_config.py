import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("INTERNAL_ADMIN_TOKEN", "admin-secret")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules import help_config

app = FastAPI()
app.include_router(help_config.router)
client = TestClient(app)


class HelpConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_atabaque_is_hidden_by_default(self):
        with patch.object(help_config, "_read_raw_row", return_value=None):
            response = client.get("/workspaces/atabaque/help-config")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["enabled"])

    def test_other_tenant_is_visible_by_default(self):
        with patch.object(help_config, "_read_raw_row", return_value=None):
            response = client.get("/workspaces/demo/help-config")
        self.assertTrue(response.json()["enabled"])

    def test_patch_requires_portal_session(self):
        response = client.patch("/workspaces/atabaque/help-config", json={})
        self.assertEqual(response.status_code, 401)

    async def test_patch_preserves_other_extra_settings(self):
        row = {"extra_settings": {"email": {"events": {}}}}
        execute = MagicMock(return_value=MagicMock(data=[]))
        upsert = MagicMock(return_value=MagicMock(execute=execute))
        table = MagicMock(return_value=MagicMock(upsert=upsert))
        body = help_config.HelpConfigPatch(
            enabled=True,
            button_label="Ajuda",
            title="Central de ajuda",
            subtitle="Respostas rápidas",
            welcome_message="Como podemos ajudar?",
            fallback_message="Fale com a equipe.",
            topics=[help_config.HelpTopic(question="Onde envio?", answer="No formulário.", keywords=["envio"])],
        )
        with (
            patch.object(help_config, "_read_raw_row", side_effect=[row, {"extra_settings": {**row["extra_settings"], "help": body.model_dump()}}]),
            patch.object(help_config.supabase, "table", table),
        ):
            result = await help_config.patch_help_config("atabaque", body, None)
        saved = upsert.call_args.args[0]
        self.assertIn("email", saved["extra_settings"])
        self.assertTrue(result["enabled"])
        self.assertEqual(result["topics"][0]["question"], "Onde envio?")


if __name__ == "__main__":
    unittest.main()
