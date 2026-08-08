import os
import json
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
        self.assertEqual(result["fields"]["track.lyrics"]["requirement"], "optional")
        self.assertIn("track.audioAnalysis", result["fields"])
        self.assertIn("track.lyricsSync", result["fields"])
        self.assertEqual(result["fields"]["track.lyricsSync"]["placeholder"], "Gerar timestamps com IA")
        self.assertIn("welcome.trackingCard", result["fields"])
        self.assertIn("project.assetGuide", result["fields"])
        self.assertIn("footer.poweredBy", result["fields"])
        self.assertEqual(result["steps"]["inicio"], "Início e apresentação")
        self.assertEqual(result["fields"]["marketingNumbers"]["requirement"], "optional")

    def test_non_atabaque_workspace_never_inherits_atabaque_copy(self):
        with patch.object(form_config, "_read_raw_row", return_value=None):
            result = form_config._resolve("sunbeat-qa-isolated", "release_intake")

        serialized = json.dumps(result, ensure_ascii=False).lower()
        self.assertNotIn("atabaque", serialized)
        self.assertEqual(
            result["fields"]["welcome.chip"]["label"],
            "Sunbeat · Operação de lançamentos",
        )
        self.assertIn("workspace", result["fields"]["project.assetGuide"]["label"])

    def test_atabaque_keeps_its_approved_production_copy(self):
        with patch.object(form_config, "_read_raw_row", return_value=None):
            result = form_config._resolve("atabaque", "release_intake")

        self.assertEqual(
            result["fields"]["welcome.chip"]["label"],
            "Atabaque · Um Ritmo de Pensar Música",
        )
        self.assertIn("pela Atabaque", result["fields"]["consentTruth"]["hint"])

    def test_resolve_uses_workflow_specific_catalogs(self):
        with patch.object(form_config, "_read_raw_row", return_value=None):
            clearance = form_config._resolve("atabaque", "rights_clearance")
            people = form_config._resolve("atabaque", "people_registry")
            company = form_config._resolve("atabaque", "company_registry")

        self.assertIn("requester_email", clearance["fields"])
        self.assertIn("tracks.primary_artists", clearance["fields"])
        self.assertNotIn("responsibleEmail", clearance["fields"])
        self.assertIn("stage_name", people["fields"])
        self.assertIn("document_number", company["fields"])
        self.assertIn("footer.poweredBy", clearance["fields"])
        self.assertIn("footer.poweredBy", people["fields"])
        self.assertIn("footer.poweredBy", company["fields"])
        self.assertEqual(company["fields"]["consentTruth"]["requirement"], "on_submit")
        self.assertTrue(clearance["fields"]["requester_email"]["locked"])

    def test_unknown_workflow_has_no_release_catalog_fallback(self):
        with self.assertRaises(HTTPException) as raised:
            form_config._resolve("atabaque", "unknown")
        self.assertEqual(raised.exception.status_code, 404)

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

    def test_resolve_ignores_stored_requirement_override_for_protected_field(self):
        row = {"extra_settings": {"form": {"fields": {
            "projectName": {"visible": False, "requirement": "optional", "label": "Projeto interno"}
        }}}}
        with patch.object(form_config, "_read_raw_row", return_value=row):
            result = form_config._resolve("atabaque", "release_intake")

        field = result["fields"]["projectName"]
        self.assertTrue(field["visible"])
        self.assertEqual(field["requirement"], "on_step")
        self.assertEqual(field["label"], "Projeto interno")

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
