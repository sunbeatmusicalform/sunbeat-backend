"""Contrato do painel de e-mails e renderização segura de templates."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("INTERNAL_ADMIN_TOKEN", "admin-secret")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.email_config import router
from app.modules.portal_session import issue_portal_token
from app.services.email import _merge_recipients, render_email_template

app = FastAPI()
app.include_router(router)
client = TestClient(app)


class EmailConfigTests(unittest.TestCase):
    def test_requires_workspace_session(self) -> None:
        response = client.get(
            "/workspaces/atabaque/workflows/release_intake/email-config"
        )
        self.assertEqual(response.status_code, 401)

    def test_get_returns_all_supported_events(self) -> None:
        token = issue_portal_token("atabaque")
        row = {
            "extra_settings": {
                "email": {
                    "events": {
                        "on_first_stage": {
                            "enabled": True,
                            "recipients": ["ops@example.com"],
                        }
                    }
                }
            }
        }
        with patch("app.modules.email_config._read_raw_row", return_value=row):
            response = client.get(
                "/workspaces/atabaque/workflows/release_intake/email-config",
                headers={"X-Portal-Token": token},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["events"]), 5)
        self.assertEqual(
            payload["events"]["on_first_stage"]["recipients"],
            ["ops@example.com"],
        )
        self.assertIn("project_title", payload["placeholders"])
        first_stage = payload["templates"]["on_first_stage"]
        self.assertEqual(
            first_stage["default_subject"],
            "Primeira etapa concluida - Novo Horizonte",
        )
        self.assertIn(
            "recebeu a conclusao da primeira etapa",
            first_stage["default_body"],
        )

    def test_effective_preview_keeps_saved_fields_separate_from_defaults(self) -> None:
        token = issue_portal_token("atabaque")
        row = {
            "extra_settings": {
                "email": {
                    "templates": {
                        "on_first_stage": {
                            "subject": "Projeto {{project_title}}",
                            "body": "<p>Olá, {{submitter_name}}</p>",
                        }
                    }
                }
            }
        }
        with patch("app.modules.email_config._read_raw_row", return_value=row):
            response = client.get(
                "/workspaces/atabaque/workflows/release_intake/email-config",
                headers={"X-Portal-Token": token},
            )
        template = response.json()["templates"]["on_first_stage"]
        self.assertEqual(template["subject"], "Projeto {{project_title}}")
        self.assertEqual(template["body"], "<p>Olá, {{submitter_name}}</p>")
        self.assertEqual(
            template["default_subject"],
            "Primeira etapa concluida - Novo Horizonte",
        )

    def test_patch_preserves_unrelated_extra_settings(self) -> None:
        token = issue_portal_token("atabaque")
        row = {
            "extra_settings": {
                "drive": {"root_folder_id": "folder-123456"},
                "email": {"events": {"on_submit": {"enabled": True, "recipients": []}}},
            }
        }
        table = MagicMock()
        table.upsert.return_value.execute.return_value.data = [{}]
        supabase = MagicMock()
        supabase.table.return_value = table
        with (
            patch("app.modules.email_config._read_raw_row", return_value=row),
            patch("app.modules.email_config.supabase", supabase),
        ):
            response = client.patch(
                "/workspaces/atabaque/workflows/release_intake/email-config",
                headers={"X-Portal-Token": token},
                json={
                    "events": {
                        "on_first_stage": {
                            "enabled": False,
                            "recipients": ["OPS@EXAMPLE.COM"],
                        }
                    },
                    "templates": {
                        "on_first_stage": {
                            "subject": "Projeto {{project_title}}",
                            "body": "<p>Olá, {{submitter_name}}</p>",
                        }
                    },
                },
            )
        self.assertEqual(response.status_code, 200)
        payload = table.upsert.call_args.args[0]
        self.assertEqual(
            payload["extra_settings"]["drive"]["root_folder_id"],
            "folder-123456",
        )
        self.assertEqual(
            payload["extra_settings"]["email"]["events"]["on_first_stage"]["recipients"],
            ["ops@example.com"],
        )

    def test_rejects_unknown_placeholder_and_unsafe_html(self) -> None:
        token = issue_portal_token("atabaque")
        path = "/workspaces/atabaque/workflows/release_intake/email-config"
        unknown = client.patch(
            path,
            headers={"X-Portal-Token": token},
            json={"templates": {"on_submit": {"subject": "{{unknown}}", "body": ""}}},
        )
        unsafe = client.patch(
            path,
            headers={"X-Portal-Token": token},
            json={"templates": {"on_submit": {"subject": "Ok", "body": "<script>x</script>"}}},
        )
        self.assertEqual(unknown.status_code, 422)
        self.assertEqual(unsafe.status_code, 422)


class EmailTemplateRenderTests(unittest.TestCase):
    def test_escapes_form_values_and_preserves_unknown_tokens(self) -> None:
        rendered = render_email_template(
            "<p>{{submitter_name}} — {{not_configured}}</p>",
            {"submitter_name": "<Ana & Bia>"},
        )
        self.assertEqual(
            rendered,
            "<p>&lt;Ana &amp; Bia&gt; — {{not_configured}}</p>",
        )

    def test_delivery_copies_are_deduplicated(self) -> None:
        self.assertEqual(
            _merge_recipients(
                ["ops@example.com", "ANA@example.com"],
                ["ops@example.com", "audit@example.com"],
                exclude=["ana@example.com"],
            ),
            ["ops@example.com", "audit@example.com"],
        )


if __name__ == "__main__":
    unittest.main()
