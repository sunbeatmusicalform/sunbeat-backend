from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import people_registry_verify as verify_service


LEGACY_RECORD = {
    "id": "recLegacy",
    "fields": {
        "Name - Cadastro": "Ana Sol",
        "Nome Completo": "Ana Maria Silva",
        "Endereço de e-mail": "ANA@EXAMPLE.COM ",
        "Idpessoa": "123.456.789-00",
    },
}
V2_RECORD = {
    "id": "recV2",
    "fields": {
        "Nome de Exibição": "Ana Sol",
        "Nome Legal / Razão Social": "Ana Maria Silva",
        "E-mail principal": "ana@example.com",
        "Documento": "12345678900",
    },
}


class PeopleRegistryVerifyTests(unittest.TestCase):
    def _verify(self, legacy_records, v2_records, query="ana@example.com"):
        responses = [
            {"records": legacy_records},
            {"records": v2_records},
        ]
        with (
            patch.object(
                verify_service,
                "get_airtable_extra_config",
                return_value={},
            ),
            patch.object(
                verify_service,
                "_request_json",
                side_effect=responses,
            ) as request_mock,
        ):
            response = verify_service.verify_people_registry_records(
                workspace_slug="atabaque",
                query=query,
            )

        self.assertEqual(
            [call.args[0] for call in request_mock.call_args_list],
            ["GET", "GET"],
        )
        self.assertTrue(
            all(call.kwargs.get("payload") is None for call in request_mock.call_args_list)
        )
        return response.model_dump(mode="json")

    def test_verdict_ambas(self) -> None:
        data = self._verify([LEGACY_RECORD], [V2_RECORD])

        self.assertEqual(data["verdict"], "ambas")
        self.assertEqual(data["acao"], "usar_v2")
        self.assertEqual(data["dados_cadastrais"]["match_by"], "email")
        self.assertEqual(data["v2_pessoas"]["match_by"], "email")

    def test_verdict_so_v2(self) -> None:
        data = self._verify([], [V2_RECORD], query="123.456.789-00")

        self.assertEqual(data["verdict"], "so_v2")
        self.assertEqual(data["acao"], "usar_v2")
        self.assertIsNone(data["dados_cadastrais"])
        self.assertEqual(data["v2_pessoas"]["match_by"], "documento")

    def test_verdict_so_legado(self) -> None:
        data = self._verify([LEGACY_RECORD], [], query="ana sol")

        self.assertEqual(data["verdict"], "so_legado")
        self.assertEqual(data["acao"], "migrar_para_v2")
        self.assertEqual(data["dados_cadastrais"]["match_by"], "nome")
        self.assertIsNone(data["v2_pessoas"])

    def test_verdict_nao_encontrado(self) -> None:
        data = self._verify([], [], query="Pessoa Inexistente")

        self.assertEqual(data["verdict"], "nao_encontrado")
        self.assertEqual(data["acao"], "criar_cadastro")
        self.assertIsNone(data["dados_cadastrais"])
        self.assertIsNone(data["v2_pessoas"])

    def test_workspace_overrides_both_table_names(self) -> None:
        with (
            patch.object(
                verify_service,
                "get_airtable_extra_config",
                return_value={
                    "base_id_override": "appOverride",
                    "people_registry_legacy_table_override": "Legado Override",
                    "people_registry_table_override": "V2 Override",
                },
            ),
            patch.object(
                verify_service,
                "_request_json",
                side_effect=[{"records": []}, {"records": []}],
            ) as request_mock,
        ):
            verify_service.verify_people_registry_records(
                workspace_slug="atabaque",
                query="Ana",
            )

        urls = [call.args[1] for call in request_mock.call_args_list]
        self.assertIn("appOverride/Legado%20Override", urls[0])
        self.assertIn("appOverride/V2%20Override", urls[1])


if __name__ == "__main__":
    unittest.main()
