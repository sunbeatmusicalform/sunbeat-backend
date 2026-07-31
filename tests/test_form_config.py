import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("INTERNAL_ADMIN_TOKEN", "admin-secret")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.modules import form_config

app = FastAPI()
app.include_router(form_config.router)
client = TestClient(app)


class FormConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_public_get_does_not_require_portal_session(self):
        with patch.object(form_config, "_read_raw_row", return_value=None):
            response = client.get(
                "/workspaces/atabaque/workflows/release_intake/form-config"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workspace_slug"], "atabaque")

    def test_patch_requires_workspace_session(self):
        response = client.patch(
            "/workspaces/atabaque/workflows/release_intake/form-config",
            json={"fields": {"marketingNumbers": {"visible": False}}},
        )
        self.assertEqual(response.status_code, 401)

    def test_resolve_returns_safe_defaults(self):
        with patch.object(form_config, "_read_raw_row", return_value=None):
            result = form_config._resolve("atabaque", "release_intake")

        self.assertEqual(result["schema_version"], 1)
        self.assertTrue(result["fields"]["responsibleEmail"]["locked"])
        self.assertEqual(result["fields"]["track.audio"]["requirement"], "on_step")
        self.assertEqual(result["fields"]["marketingNumbers"]["requirement"], "optional")

    def test_resolve_merges_only_supported_overrides(self):
        row = {
            "extra_settings": {
                "form": {
                    "fields": {
                        "marketingNumbers": {
                            "visible": False,
                            "requirement": "optional",
                            "label": "Histórico do projeto",
                        }
                    }
                }
            }
        }
        with patch.object(form_config, "_read_raw_row", return_value=row):
            result = form_config._resolve("atabaque", "release_intake")

        field = result["fields"]["marketingNumbers"]
        self.assertFalse(field["visible"])
        self.assertEqual(field["label"], "Histórico do projeto")
        self.assertEqual(field["_origin"], "db")

    async def test_patch_preserves_other_extra_settings(self):
        row = {"extra_settings": {"email": {"events": {"on_draft": {"enabled": True}}}}}
        execute = MagicMock(return_value=MagicMock(data=[]))
        upsert = MagicMock(return_value=MagicMock(execute=execute))
        table = MagicMock(return_value=MagicMock(upsert=upsert))
        body = form_config.FormConfigPatch(
            fields={
                "marketingNumbers": form_config.FormFieldPatch(
                    visible=False,
                    label="Histórico e resultados",
                )
            }
        )

        with (
            patch.object(form_config, "_read_raw_row", side_effect=[row, row]),
            patch.object(form_config.supabase, "table", table),
        ):
            result = await form_config.patch_form_config(
                "atabaque", "release_intake", body, None
            )

        saved = upsert.call_args.args[0]
        self.assertIn("email", saved["extra_settings"])
        self.assertFalse(saved["extra_settings"]["form"]["fields"]["marketingNumbers"]["visible"])
        self.assertIn("marketingNumbers", result["updated"])

    async def test_patch_rejects_unlocking_protected_field(self):
        body = form_config.FormConfigPatch(
            fields={
                "responsibleEmail": form_config.FormFieldPatch(requirement="optional")
            }
        )
        with patch.object(form_config, "_read_raw_row", return_value=None):
            with self.assertRaises(HTTPException) as raised:
                await form_config.patch_form_config(
                    "atabaque", "release_intake", body, None
                )
        self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
