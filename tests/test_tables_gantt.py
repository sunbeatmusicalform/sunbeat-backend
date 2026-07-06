from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.modules.tables import router
from app.services import tables_gantt


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TablesGanttTests(unittest.TestCase):
    def test_project_fallback_builds_macroarea_dates(self) -> None:
        project_record = {
            "id": "recProject1",
            "fields": {
                "Nome do Projeto": "Album Raizes",
                "Data de Lançamento": "2026-09-01",
                "Tem Videoclipe / Lyric / Visualizer": "Não",
            },
        }

        def list_records(*, table_name: str, **_kwargs):
            if table_name == "[V2] Etapas do Lançamento":
                raise RuntimeError("missing stages table")
            return [project_record]

        with (
            patch.object(tables_gantt, "_list_airtable_records", side_effect=list_records),
            patch.object(tables_gantt, "_base_id", return_value=None),
        ):
            response = tables_gantt.build_gantt_response(
                workspace_slug="atabaque",
                max_records=10,
            )

        self.assertTrue(response["ok"])
        self.assertEqual(response["source"], "projects_fallback")
        clearance = next(item for item in response["items"] if item["macroarea"] == "Clearance")
        self.assertEqual(clearance["project_name"], "Album Raizes")
        self.assertEqual(clearance["start_date"], "2026-07-03")
        self.assertEqual(clearance["end_date"], "2026-08-11")
        self.assertFalse(any(item["macroarea"] == "Videoclipe" for item in response["items"]))

    def test_stage_records_are_normalized(self) -> None:
        stage_record = {
            "id": "recStage1",
            "fields": {
                "Nome da Etapa": "Clearance",
                "Projeto": "Album Raizes",
                "Macroárea": "Clearance",
                "Data Início": "2026-07-03",
                "Data Fim": "2026-08-11",
                "Status": "Concluída",
                "Responsável": "Henrique",
                "Ativa": True,
            },
        }

        with (
            patch.object(tables_gantt, "_list_airtable_records", return_value=[stage_record]),
            patch.object(tables_gantt, "_base_id", return_value=None),
        ):
            response = tables_gantt.build_gantt_response(
                workspace_slug="atabaque",
                max_records=10,
            )

        self.assertEqual(response["source"], "stages")
        self.assertEqual(response["items"][0]["status"], "concluida")
        self.assertEqual(response["items"][0]["responsible"], "Henrique")

    def test_gantt_route_requires_admin_token(self) -> None:
        with patch.object(settings, "INTERNAL_ADMIN_TOKEN", "secret"):
            response = _client().get("/tables/atabaque/gantt")

        self.assertEqual(response.status_code, 401)

    def test_gantt_route_accepts_admin_token(self) -> None:
        with (
            patch.object(settings, "INTERNAL_ADMIN_TOKEN", "secret"),
            patch(
                "app.modules.tables.build_gantt_response",
                return_value={
                    "ok": True,
                    "workspace_slug": "atabaque",
                    "source": "stages",
                    "items": [],
                    "summary": {"total": 0},
                    "filters": {},
                    "warnings": [],
                },
            ),
        ):
            response = _client().get(
                "/tables/atabaque/gantt",
                headers={"X-Admin-Token": "secret"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])


if __name__ == "__main__":
    unittest.main()
