from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def _build_frontend_workflow_path(*, workspace_slug: str, workflow_type: str | None = None) -> str:
    prefixes = {
        "release_intake": "/intake",
        "rights_clearance": "/clearance",
        "people_registry": "/people",
        "company_registry": "/company",
    }
    return f"{prefixes.get(workflow_type or 'release_intake', f'/{workflow_type}')}/{workspace_slug}"


def _load_email_service() -> types.ModuleType:
    service_path = Path(__file__).resolve().parents[1] / "app" / "services" / "email.py"
    module_name = "_test_email_workflow_urls_service"
    spec = importlib.util.spec_from_file_location(module_name, service_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load email service spec")

    stubs = {
        "app.core.config": types.SimpleNamespace(
            settings=types.SimpleNamespace(
                FRONTEND_BASE_URL="https://sunbeat.pro",
                RESEND_API_KEY="key-123",
                RESEND_FROM_EMAIL="ops@sunbeat.pro",
            )
        ),
        "app.services.workspace_config": types.SimpleNamespace(
            get_email_extra_config=lambda *_args, **_kwargs: {},
            get_email_event_config=lambda *_args, **_kwargs: {"enabled": True},
        ),
        "app.modules.workflow_registry": types.SimpleNamespace(
            build_frontend_workflow_path=_build_frontend_workflow_path,
        ),
    }
    original_modules = {name: sys.modules.get(name) for name in stubs}
    try:
        sys.modules.update(stubs)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


email_service = _load_email_service()


class EmailWorkflowUrlTests(unittest.TestCase):
    def test_legacy_build_edit_url_stays_on_intake(self) -> None:
        self.assertEqual(
            email_service.build_edit_url("edit-123", "atabaque"),
            "https://sunbeat.pro/intake/atabaque?edit_token=edit-123",
        )

    def test_build_workflow_edit_url_routes_known_workflows(self) -> None:
        self.assertEqual(
            email_service.build_workflow_edit_url(
                edit_token="edit-123",
                workspace_slug="atabaque",
                workflow_type="release_intake",
            ),
            "https://sunbeat.pro/intake/atabaque?edit_token=edit-123",
        )
        self.assertEqual(
            email_service.build_workflow_edit_url(
                edit_token="edit-123",
                workspace_slug="atabaque",
                workflow_type="rights_clearance",
            ),
            "https://sunbeat.pro/clearance/atabaque?edit_token=edit-123",
        )
        self.assertEqual(
            email_service.build_workflow_edit_url(
                edit_token="edit-123",
                workspace_slug="atabaque",
                workflow_type="people_registry",
            ),
            "https://sunbeat.pro/people/atabaque?edit_token=edit-123",
        )

    def test_send_edit_link_email_only_opts_clearance_into_workflow_url(self) -> None:
        with patch.object(
            email_service,
            "_post_resend",
            return_value={"provider_message_id": "msg-123"},
        ) as resend_mock:
            email_service.send_edit_link_email(
                to_email="ana@example.com",
                edit_token="edit-clearance",
                project_title="Projeto Direitos",
                workspace_slug="atabaque",
                workflow_type="rights_clearance",
            )

        self.assertEqual(
            resend_mock.call_args.kwargs["edit_url"],
            "https://sunbeat.pro/clearance/atabaque?edit_token=edit-clearance",
        )

        with patch.object(
            email_service,
            "_post_resend",
            return_value={"provider_message_id": "msg-456"},
        ) as resend_mock:
            email_service.send_edit_link_email(
                to_email="ana@example.com",
                edit_token="edit-company",
                project_title="Empresa Teste",
                workspace_slug="atabaque",
                workflow_type="company_registry",
            )

        self.assertEqual(
            resend_mock.call_args.kwargs["edit_url"],
            "https://sunbeat.pro/intake/atabaque?edit_token=edit-company",
        )


if __name__ == "__main__":
    unittest.main()
