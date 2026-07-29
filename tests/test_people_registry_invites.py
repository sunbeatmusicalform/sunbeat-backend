from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.schemas.people_registry import (
    PeopleRegistryInviteEmailPayload,
    PeopleRegistryInviteSubmitPayload,
    PeopleRegistryPayload,
    PeopleRegistryRecordPayload,
    PeopleRegistryResponsePayload,
)
from app.services import people_registry as people_registry_service
from app.services import people_registry_invites as invite_service


def _person_payload() -> PeopleRegistryPayload:
    return PeopleRegistryPayload.model_validate(
        {
            "workspace_slug": "atabaque",
            "workflow_type": "people_registry",
            "profile": "atabaque_people_v1",
            "party": {
                "party_kind": "pf",
                "display_name": "Ana Sol",
                "legal_name": "Ana Maria Silva",
                "stage_name": "Ana Sol",
                "document_id": "123.456.789-00",
                "roles": ["artist"],
            },
            "contact": {
                "email_primary": "ana@example.com",
                "phone_primary": "+5581999999999",
            },
        }
    )


class PeopleRegistryInviteTests(unittest.TestCase):
    def test_build_clearance_parte_patch_fields_uses_translated_fields(self) -> None:
        prepared = people_registry_service.normalize_people_registry_payload(
            _person_payload()
        )
        submit_payload = PeopleRegistryInviteSubmitPayload.model_validate(
            {
                "person": _person_payload().model_dump(mode="json"),
                "participation": {
                    "confirmation_status": "em_negociacao",
                    "musical_role": "Intérprete / Artista",
                    "remuneration_type": "Percentual",
                    "participation_percent": 25,
                    "fixed_amount": None,
                    "notes": "Percentual precisa ser revisado.",
                },
            }
        )

        fields = invite_service._build_clearance_parte_patch_fields(
            people_airtable_record_id="recPessoa123",
            prepared=prepared,
            payload=submit_payload,
        )

        self.assertEqual(fields["Pessoa Vinculada"], ["recPessoa123"])
        self.assertEqual(fields["Status do Cadastro"], "Completo")
        self.assertEqual(fields["Canal de Comunicação"], "Formulário Sunbeat")
        self.assertEqual(fields["CPF / CNPJ"], "123.456.789-00")
        self.assertEqual(fields["E-mail de Assinatura"], "ana@example.com")
        self.assertEqual(fields["Telefone de Assinatura"], "+5581999999999")
        self.assertEqual(fields["Função Musical no Clearance"], "Intérprete / Artista")
        self.assertEqual(fields["Tipo de Remuneração"], "Percentual")
        self.assertEqual(fields["Percentual / Participação"], 0.25)
        self.assertEqual(fields["Status de Aprovação"], "Em negociação")
        self.assertNotIn("Clearance Case", fields)
        self.assertNotIn("Person", fields)

    def test_submit_invite_links_people_airtable_record_to_clearance_part(self) -> None:
        person = _person_payload()
        prepared = people_registry_service.normalize_people_registry_payload(person)
        response = PeopleRegistryResponsePayload(
            ok=True,
            status="created",
            data=prepared,
            record=PeopleRegistryRecordPayload(
                record_id="local-person-1",
                airtable_sync_status="synced",
                created_at="2026-07-02T12:00:00+00:00",
                updated_at="2026-07-02T12:00:01+00:00",
            ),
        )
        invite_row = {
            "token": "invite-token",
            "workspace_slug": "atabaque",
            "profile": "atabaque_people_v1",
            "status": "opened",
            "airtable_clearance_part_id": "recParte123",
            "context": {"clearance_case_name": "Caso Teste"},
            "created_at": "2026-07-02T12:00:00+00:00",
            "updated_at": "2026-07-02T12:00:00+00:00",
        }

        with (
            patch.object(
                invite_service,
                "fetch_people_registry_invite_by_token",
                return_value=invite_row,
            ),
            patch.object(
                invite_service,
                "create_people_registry_record_response",
                return_value=response,
            ),
            patch.object(
                invite_service,
                "fetch_people_registry_record_by_id",
                return_value={"id": "local-person-1", "airtable_record_id": "recPessoa123"},
            ),
            patch.object(invite_service, "_patch_clearance_parte") as patch_parte,
            patch.object(
                invite_service,
                "_update_invite",
                side_effect=lambda _token, fields: {**invite_row, **fields},
            ),
        ):
            result = invite_service.submit_people_registry_invite_response(
                "invite-token",
                PeopleRegistryInviteSubmitPayload(
                    person=person,
                    participation={
                        "confirmation_status": "confirmado",
                        "musical_role": "Autor / Compositor",
                        "remuneration_type": "Percentual",
                        "participation_percent": 50,
                    },
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "submitted")
        patch_parte.assert_called_once()
        self.assertEqual(patch_parte.call_args.kwargs["workspace_slug"], "atabaque")
        self.assertEqual(
            patch_parte.call_args.kwargs["airtable_clearance_part_id"],
            "recParte123",
        )
        self.assertEqual(
            patch_parte.call_args.kwargs["fields"]["Pessoa Vinculada"],
            ["recPessoa123"],
        )
        self.assertEqual(
            patch_parte.call_args.kwargs["fields"]["Percentual / Participação"],
            0.5,
        )

    def test_submit_manual_invite_without_clearance_part_does_not_patch_airtable(self) -> None:
        person = _person_payload()
        prepared = people_registry_service.normalize_people_registry_payload(person)
        response = PeopleRegistryResponsePayload(
            ok=True,
            status="created",
            data=prepared,
            record=PeopleRegistryRecordPayload(
                record_id="local-person-1",
                airtable_sync_status="synced",
                created_at="2026-07-02T12:00:00+00:00",
                updated_at="2026-07-02T12:00:01+00:00",
            ),
        )
        invite_row = {
            "token": "manual-invite-token",
            "workspace_slug": "atabaque",
            "profile": "atabaque_people_v1",
            "status": "opened",
            "airtable_clearance_part_id": "",
            "context": {"project_title": "Projeto Teste"},
            "created_at": "2026-07-02T12:00:00+00:00",
            "updated_at": "2026-07-02T12:00:00+00:00",
        }

        with (
            patch.object(
                invite_service,
                "fetch_people_registry_invite_by_token",
                return_value=invite_row,
            ),
            patch.object(
                invite_service,
                "create_people_registry_record_response",
                return_value=response,
            ),
            patch.object(
                invite_service,
                "fetch_people_registry_record_by_id",
                return_value={"id": "local-person-1", "airtable_record_id": "recPessoa123"},
            ),
            patch.object(invite_service, "_patch_clearance_parte") as patch_parte,
            patch.object(
                invite_service,
                "_update_invite",
                side_effect=lambda _token, fields: {**invite_row, **fields},
            ),
        ):
            result = invite_service.submit_people_registry_invite_response(
                "manual-invite-token",
                PeopleRegistryInviteSubmitPayload(
                    person=person,
                    participation={
                        "confirmation_status": "confirmado",
                        "musical_role": "Autor / Compositor",
                        "remuneration_type": "Percentual",
                        "participation_percent": 50,
                    },
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "submitted")
        patch_parte.assert_not_called()

    def test_send_invite_email_uses_context_recipient(self) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        invite_row = {
            "token": "email-invite-token",
            "workspace_slug": "atabaque",
            "profile": "atabaque_people_v1",
            "status": "pending",
            "airtable_clearance_part_id": "",
            "context": {
                "party_name": "Ana Sol",
                "signing_email": "ana@example.com",
                "project_title": "Projeto Teste",
                "track_title": "Faixa Teste",
                "requested_role": "Intérprete / Artista",
                "remuneration": "25%",
            },
            "expires_at": expires_at,
            "created_at": "2026-07-02T12:00:00+00:00",
            "updated_at": "2026-07-02T12:00:00+00:00",
        }

        with (
            patch.object(
                invite_service,
                "fetch_people_registry_invite_by_token",
                return_value=invite_row,
            ),
            patch.object(
                invite_service,
                "send_people_registry_invite_email",
                return_value={"provider_message_id": "msg_123"},
            ) as send_email,
            patch.object(
                invite_service,
                "_update_invite",
                side_effect=lambda _token, fields: {
                    **invite_row,
                    **fields,
                    "context": fields.get("context", invite_row["context"]),
                },
            ),
        ):
            result = invite_service.send_people_registry_invite_email_response(
                "email-invite-token",
                PeopleRegistryInviteEmailPayload(workspace_slug="atabaque"),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.invite.status if result.invite else None, "sent")
        self.assertEqual(result.provider_message_id, "msg_123")
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.kwargs["to_email"], "ana@example.com")
        self.assertEqual(send_email.call_args.kwargs["recipient_name"], "Ana Sol")
        self.assertEqual(send_email.call_args.kwargs["project_title"], "Projeto Teste")

    def test_send_invite_email_rejects_workspace_mismatch(self) -> None:
        invite_row = {
            "token": "email-invite-token",
            "workspace_slug": "atabaque",
            "profile": "atabaque_people_v1",
            "status": "pending",
            "airtable_clearance_part_id": "",
            "context": {"signing_email": "ana@example.com"},
            "created_at": "2026-07-02T12:00:00+00:00",
            "updated_at": "2026-07-02T12:00:00+00:00",
        }

        with (
            patch.object(
                invite_service,
                "fetch_people_registry_invite_by_token",
                return_value=invite_row,
            ),
            patch.object(invite_service, "send_people_registry_invite_email") as send_email,
        ):
            result = invite_service.send_people_registry_invite_email_response(
                "email-invite-token",
                PeopleRegistryInviteEmailPayload(workspace_slug="outro-workspace"),
            )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.error.code if result.error else None,
            "people_registry_invite_workspace_mismatch",
        )
        send_email.assert_not_called()

    def test_discontinued_invite_is_terminal(self) -> None:
        invite_row = {
            "token": "discontinued-token",
            "workspace_slug": "atabaque",
            "profile": "atabaque_people_v1",
            "status": "discontinued",
            "airtable_clearance_part_id": "",
            "context": {"party_name": "Ana Sol", "signing_email": "ana@example.com"},
            "created_at": "2026-07-02T12:00:00+00:00",
            "updated_at": "2026-07-02T12:00:00+00:00",
        }

        with patch.object(
            invite_service,
            "fetch_people_registry_invite_by_token",
            return_value=invite_row,
        ):
            read_result = invite_service.get_people_registry_invite_response(
                "discontinued-token"
            )

        self.assertFalse(read_result.ok)
        self.assertEqual(read_result.status, "discontinued")
        self.assertEqual(
            read_result.error.code if read_result.error else None,
            "people_registry_invite_discontinued",
        )

        with (
            patch.object(
                invite_service,
                "fetch_people_registry_invite_by_token",
                return_value=invite_row,
            ),
            patch.object(invite_service, "send_people_registry_invite_email") as send_email,
        ):
            email_result = invite_service.send_people_registry_invite_email_response(
                "discontinued-token",
                PeopleRegistryInviteEmailPayload(workspace_slug="atabaque"),
            )

        self.assertFalse(email_result.ok)
        send_email.assert_not_called()

        with (
            patch.object(
                invite_service,
                "fetch_people_registry_invite_by_token",
                return_value=invite_row,
            ),
            patch.object(invite_service, "create_people_registry_record_response") as create_person,
        ):
            submit_result = invite_service.submit_people_registry_invite_response(
                "discontinued-token",
                PeopleRegistryInviteSubmitPayload(person=_person_payload()),
            )

        self.assertFalse(submit_result.ok)
        self.assertEqual(submit_result.status, "discontinued")
        create_person.assert_not_called()


if __name__ == "__main__":
    unittest.main()
