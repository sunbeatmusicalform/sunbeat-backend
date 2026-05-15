from __future__ import annotations

import os
import sys
import types
import unittest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")

try:
    import pyairtable  # noqa: F401
except ModuleNotFoundError:
    pyairtable_stub = types.ModuleType("pyairtable")

    class _Api:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    pyairtable_stub.Api = _Api
    sys.modules["pyairtable"] = pyairtable_stub

from app.schemas.submission import CompanyRegistrySubmissionPayload
from app.services import airtable_company_registry as sync_service


def _payload() -> CompanyRegistrySubmissionPayload:
    return CompanyRegistrySubmissionPayload.model_validate(
        {
            "draft_token": "draft-1",
            "workspace_slug": "atabaque",
            "workflow_type": "company_registry",
            "company_data": {
                "document_type": "cnpj",
                "document_number": "12.345.678/0001-90",
                "fantasy_name": "Empresa Teste",
                "legal_name": "Empresa Teste LTDA",
                "address": "Rua Teste, 123",
                "city": "Sao Paulo",
                "state": "SP",
                "zip_code": "01000-000",
            },
            "legal_representative": {
                "name": "Ana Legal",
                "phone": "+5511999999999",
                "email": "legal@example.com",
            },
            "contract_representative": {
                "same_as_legal": "yes",
            },
            "financial_representative": {
                "same_as_contract": "yes",
            },
            "banking_data": {
                "bank_name": "Banco Teste",
                "agency": "1234",
                "account": "99999-0",
                "account_type": "corrente",
                "pix_key": "pix@example.com",
            },
        }
    )


class CompanyRegistryAirtableSyncTests(unittest.TestCase):
    def test_build_fields_targets_v2_empresas_columns(self) -> None:
        fields = sync_service._build_public_fields(_payload())

        self.assertEqual(fields["Nome fantasia"], "Empresa Teste")
        self.assertEqual(fields["Tipo de documento"], "cnpj")
        self.assertEqual(fields["Número do documento"], "12.345.678/0001-90")
        self.assertEqual(fields["Razão social"], "Empresa Teste LTDA")
        self.assertEqual(fields["Endereço"], "Rua Teste, 123")
        self.assertEqual(fields["Nome do responsável legal"], "Ana Legal")
        self.assertEqual(fields["Mesmo que o responsável legal? (Contrato)"], "yes")
        self.assertEqual(fields["Nome do responsável pelo contrato"], "Ana Legal")
        self.assertEqual(fields["Mesmo que o responsável pelo contrato?"], "yes")
        self.assertEqual(fields["Nome do responsável financeiro"], "Ana Legal")
        self.assertEqual(fields["Agência"], "1234")
        self.assertEqual(fields["Tipo de conta"], "corrente")
        self.assertNotIn("Workspace", fields)
        self.assertNotIn("Draft token", fields)

    def test_resolve_table_name_defaults_to_v2_empresas(self) -> None:
        self.assertEqual(sync_service._resolve_table_name({}), "[V2] - Empresas")


if __name__ == "__main__":
    unittest.main()
