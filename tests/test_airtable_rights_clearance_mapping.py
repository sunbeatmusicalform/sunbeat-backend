from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


def _load_service() -> types.ModuleType:
    service_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "airtable_rights_clearance.py"
    )
    module_name = "_test_airtable_rights_clearance_mapping_service"
    spec = importlib.util.spec_from_file_location(module_name, service_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load airtable_rights_clearance service spec")

    stubs = {
        "app.core.config": types.SimpleNamespace(
            settings=types.SimpleNamespace(
                AIRTABLE_RIGHTS_CLEARANCE_MUSICAL_ENABLED=True,
                FRONTEND_BASE_URL="https://sunbeat.pro",
            )
        ),
        "app.services.airtable": types.SimpleNamespace(
            _base_id=lambda: "appBase",
            _request_json=lambda *_args, **_kwargs: {},
        ),
        "app.services.workspace_config": types.SimpleNamespace(
            get_airtable_extra_config=lambda *_args, **_kwargs: {},
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


sync_service = _load_service()


class AirtableRightsClearanceMappingTests(unittest.TestCase):
    def test_case_mapping_targets_translated_v2_clearance_fields(self) -> None:
        fields = sync_service._build_record_fields(
            clearance_format="music_release_clearance_intake",
            requester={
                "requester_name": "Ana",
                "requester_email": "ana@example.com",
                "requester_company": "Atabaque",
            },
            project_context={
                "project_title": "Projeto Direitos",
                "client_or_distributor": "Distribuidora X",
            },
            clearance_scope={
                "territory": "Brasil",
                "licensing_period": "12 meses",
                "intended_use": "Lancamento musical",
            },
            assets_references={},
            submission_id="sub-123",
            edit_url="https://sunbeat.pro/clearance/atabaque?edit_token=abc",
        )

        self.assertEqual(
            fields["Nome do Caso"],
            "Clearance – Lançamento Musical - Projeto Direitos",
        )
        self.assertEqual(
            fields["Formato do Clearance"],
            "music_release_clearance_intake",
        )
        self.assertEqual(fields["Nome do Solicitante"], "Ana")
        self.assertEqual(fields["E-mail do Solicitante"], "ana@example.com")
        self.assertEqual(fields["Empresa do Solicitante"], "Atabaque")
        self.assertEqual(fields["Título do Projeto / Campanha"], "Projeto Direitos")
        self.assertEqual(fields["Status da Sincronização Airtable"], "synced")
        self.assertEqual(fields["ID da Submissão"], "sub-123")
        self.assertEqual(
            fields["URL de Edição"],
            "https://sunbeat.pro/clearance/atabaque?edit_token=abc",
        )
        self.assertNotIn("Nome do Case", fields)
        self.assertNotIn("Clearance Format", fields)
        self.assertNotIn("Solicitante Nome", fields)
        self.assertNotIn("Airtable Sync Status", fields)

    def test_item_and_party_mapping_targets_translated_link_fields(self) -> None:
        item_fields = sync_service._build_item_fields(
            {
                "title": "Faixa Direitos",
                "has_isrc": "yes",
                "isrc_code": "BR-ABC-26-00001",
            },
            "recCase",
        )

        parte_fields = sync_service._build_parte_fields(
            "Ana Sol",
            "Artista",
            "recCase",
            item_id="recItem",
            email="ana@example.com",
        )

        self.assertEqual(item_fields["Caso de Clearance"], ["recCase"])
        self.assertEqual(parte_fields["Nome da Parte no Caso"], "Ana Sol")
        self.assertEqual(parte_fields["Caso de Clearance"], ["recCase"])
        self.assertEqual(parte_fields["Item do Caso"], ["recItem"])
        self.assertEqual(parte_fields["Papel no Caso"], "Artista")
        self.assertEqual(parte_fields["E-mail de Assinatura"], "ana@example.com")
        self.assertNotIn("Clearance Case", item_fields)
        self.assertNotIn("Clearance Case", parte_fields)
        self.assertNotIn("Clearance Item", parte_fields)
        self.assertNotIn("Email de Assinatura", parte_fields)


if __name__ == "__main__":
    unittest.main()
