from __future__ import annotations

import unittest
from unittest.mock import patch

from app.schemas.people_registry import PeopleRegistryPayload, PeopleRegistryRecordPayload
from app.services import people_registry as people_registry_service
from app.services import people_registry_airtable_sync as sync_service


def _payload(profile: str = "atabaque_people_v1") -> PeopleRegistryPayload:
    return PeopleRegistryPayload.model_validate(
        {
            "workspace_slug": "atabaque",
            "workflow_type": "people_registry",
            "profile": profile,
            "party": {
                "party_kind": "pf",
                "display_name": "Ana Sol",
                "legal_name": "Ana Maria Silva",
                "stage_name": "Ana Sol",
                "document_id": "123.456.789-00",
                "roles": ["artist", "responsible"],
            },
            "contact": {
                "email_primary": "ana@example.com",
                "phone_primary": "+5511999999999",
            },
            "banking": {
                "bank_name": "Banco Teste",
                "bank_agency": "1234",
                "account_number": "99999-0",
                "account_holder_name": "Ana Maria Silva",
                "account_holder_document_id": "123.456.789-00",
                "pix_key": "ana@example.com",
            },
            "additional_info": {
                "notes_internal": "Contato principal",
            },
        }
    )


class PeopleRegistryAirtableSyncTests(unittest.TestCase):
    def test_sync_is_blocked_when_profile_toggle_is_disabled(self) -> None:
        prepared = people_registry_service.normalize_people_registry_payload(_payload())

        with (
            patch.object(sync_service.settings, "AIRTABLE_PEOPLE_REGISTRY_SYNC_ENABLED", True),
            patch.object(sync_service.settings, "AIRTABLE_PEOPLE_REGISTRY_ATABAQUE_ENABLED", False),
            patch.object(sync_service, "_update_local_sync_state") as update_state_mock,
        ):
            result = sync_service.sync_people_registry_record_to_airtable(
                record_id="rec-local-1",
                prepared=prepared,
            )

        self.assertEqual(result.status, "blocked")
        self.assertIn("Atabaque", result.error or "")
        update_state_mock.assert_called_once()
        self.assertEqual(update_state_mock.call_args.kwargs["status"], "blocked")

    def test_sync_uses_document_then_email_fallback_for_upsert(self) -> None:
        prepared = people_registry_service.normalize_people_registry_payload(_payload())
        request_side_effect = [
            {"records": []},
            {"records": [{"id": "recAirtable123", "fields": {}}]},
            {"id": "recAirtable123", "fields": {}},
        ]

        with (
            patch.object(sync_service.settings, "AIRTABLE_PEOPLE_REGISTRY_SYNC_ENABLED", True),
            patch.object(sync_service.settings, "AIRTABLE_PEOPLE_REGISTRY_ATABAQUE_ENABLED", True),
            patch.object(sync_service.settings, "AIRTABLE_PEOPLE_REGISTRY_BASE_ID", "appBase123"),
            patch.object(sync_service.settings, "AIRTABLE_PEOPLE_REGISTRY_ATABAQUE_TABLE", "Dados Cadastrais"),
            patch.object(sync_service.settings, "AIRTABLE_API_KEY", "key123"),
            patch.object(sync_service, "_request_json", side_effect=request_side_effect) as request_mock,
            patch.object(sync_service, "_update_local_sync_state") as update_state_mock,
        ):
            result = sync_service.sync_people_registry_record_to_airtable(
                record_id="rec-local-2",
                prepared=prepared,
            )

        self.assertEqual(result.status, "synced")
        self.assertEqual(result.airtable_record_id, "recAirtable123")
        self.assertEqual(result.action, "updated")
        self.assertEqual(result.merge_key, "email_primary")
        self.assertIn(
            "SUBSTITUTE({CPF / CNPJ}",
            request_mock.call_args_list[0].kwargs["params"]["filterByFormula"],
        )
        self.assertIn(
            "LOWER({Endereço de e-mail})",
            request_mock.call_args_list[1].kwargs["params"]["filterByFormula"],
        )
        self.assertEqual(update_state_mock.call_args.kwargs["status"], "synced")

    def test_create_response_runs_sync_after_persist_and_returns_updated_status(self) -> None:
        payload = _payload()
        stored_row = {
            "id": "rec-local-3",
            "airtable_sync_status": "synced",
            "created_at": "2026-04-17T00:00:00+00:00",
            "updated_at": "2026-04-17T00:00:01+00:00",
        }

        with (
            patch.object(
                people_registry_service,
                "find_people_registry_duplicate_record",
                return_value=None,
            ),
            patch.object(
                people_registry_service,
                "persist_people_registry_prepared_payload",
                return_value=PeopleRegistryRecordPayload(
                    record_id="rec-local-3",
                    airtable_sync_status="pending",
                    created_at="2026-04-17T00:00:00+00:00",
                    updated_at="2026-04-17T00:00:00+00:00",
                ),
            ),
            patch.object(
                people_registry_service,
                "sync_people_registry_record_to_airtable",
            ) as sync_mock,
            patch.object(
                people_registry_service,
                "fetch_people_registry_record_by_id",
                return_value=stored_row,
            ),
        ):
            response = people_registry_service.create_people_registry_record_response(
                payload
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.status, "created")
        self.assertIsNotNone(response.record)
        self.assertEqual(response.record.airtable_sync_status, "synced")
        sync_mock.assert_called_once()
        self.assertEqual(sync_mock.call_args.kwargs["record_id"], "rec-local-3")


if __name__ == "__main__":
    unittest.main()
