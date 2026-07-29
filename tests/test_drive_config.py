from __future__ import annotations

import os
import sys
import types
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")
os.environ.setdefault("INTERNAL_ADMIN_TOKEN", "test-admin-token")

try:
    import supabase  # noqa: F401
except ModuleNotFoundError:
    supabase_stub = types.ModuleType("supabase")
    supabase_stub.create_client = lambda *_args, **_kwargs: object()
    sys.modules["supabase"] = supabase_stub

from fastapi.testclient import TestClient

from app.core.database import supabase as real_supabase  # noqa: F401  (import side effect)
from app.main import app
from app.modules import admin_config, drive_config

ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}


class _FakeTable:
    def __init__(self, owner: "_FakeSupabase") -> None:
        self.owner = owner
        self.filters: list[tuple[str, object]] = []
        self._pending_upsert: dict | None = None

    def select(self, _fields: str) -> "_FakeTable":
        return self

    def eq(self, key: str, value: object) -> "_FakeTable":
        self.filters.append((key, value))
        return self

    def limit(self, _count: int) -> "_FakeTable":
        return self

    def upsert(self, payload: dict, on_conflict: str) -> "_FakeTable":
        self._pending_upsert = deepcopy(payload)
        return self

    def execute(self) -> SimpleNamespace:
        if self._pending_upsert is not None:
            payload = self._pending_upsert
            self._pending_upsert = None
            kept = [
                row
                for row in self.owner.rows
                if not (
                    row.get("workspace_slug") == payload.get("workspace_slug")
                    and row.get("workflow_type") == payload.get("workflow_type")
                )
            ]
            # mutação in-place: quem segura a referência externa da lista vê a atualização
            self.owner.rows[:] = kept + [payload]
            return SimpleNamespace(data=[deepcopy(payload)])

        matched = [
            deepcopy(row)
            for row in self.owner.rows
            if all(row.get(key) == value for key, value in self.filters)
        ]
        return SimpleNamespace(data=matched)


class _FakeSupabase:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def table(self, name: str) -> _FakeTable:
        if name != "workspace_workflow_settings":
            raise AssertionError(f"Unexpected table: {name}")
        return _FakeTable(self)


def _client(rows: list[dict]) -> TestClient:
    fake = _FakeSupabase(rows)
    # _read_raw_row usa o supabase de admin_config; o upsert usa o de drive_config.
    # O token é fixado no settings porque a suíte completa pode ter importado
    # app.core.config antes com outro valor de INTERNAL_ADMIN_TOKEN.
    p1 = patch.object(admin_config, "supabase", fake)
    p2 = patch.object(drive_config, "supabase", fake)
    p3 = patch.object(admin_config.settings, "INTERNAL_ADMIN_TOKEN", "test-admin-token")
    p1.start()
    p2.start()
    p3.start()
    client = TestClient(app)
    client._drive_config_patchers = (p1, p2, p3)  # type: ignore[attr-defined]
    return client


class DriveConfigGetTests(unittest.TestCase):
    def test_get_requires_admin_token(self) -> None:
        client = TestClient(app)
        res = client.get("/workspaces/atabaque/workflows/rights_clearance/drive-config")
        self.assertEqual(res.status_code, 401)

    def test_get_unknown_workflow_404(self) -> None:
        client = _client([])
        res = client.get(
            "/workspaces/atabaque/workflows/nao_existe/drive-config",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(res.status_code, 404)

    def test_get_returns_defaults_when_no_row(self) -> None:
        client = _client([])
        res = client.get(
            "/workspaces/atabaque/workflows/release_intake/drive-config",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["workflow_type"], "release_intake")
        self.assertEqual(body["root_mode"], "mirror_v2_clientes")
        self.assertEqual(body["artist_folder_pattern"], "Clientes/{Artista}/Projetos")
        self.assertEqual(body["subfolders"], ["Audios", "Capa", "Imprensa", "Imagens e Videos", "Outros"])
        self.assertEqual(body["_origin"], "default")
        self.assertEqual(body["warnings"], [])

    def test_get_merges_db_row_over_defaults(self) -> None:
        rows = [
            {
                "workspace_slug": "atabaque",
                "workflow_type": "rights_clearance",
                "extra_settings": {
                    "drive": {
                        "subfolders": ["01 Documentos", "02 Contratos"],
                        "clearance_musical_root_override": "1AbCdEfGhIjKlMnOp",
                        "_service": "app.services.google_drive.sync_clearance_to_google_drive",
                    }
                },
            }
        ]
        client = _client(rows)
        res = client.get(
            "/workspaces/atabaque/workflows/rights_clearance/drive-config",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["_origin"], "db")
        self.assertEqual(body["subfolders"], ["01 Documentos", "02 Contratos"])
        self.assertEqual(body["overrides"]["clearance_musical_root_override"], "1AbCdEfGhIjKlMnOp")
        # chaves descritivas não vazam para o painel
        self.assertNotIn("_service", body["overrides"])

    def test_get_warns_on_invalid_folder_id(self) -> None:
        rows = [
            {
                "workspace_slug": "atabaque",
                "workflow_type": "rights_clearance",
                "extra_settings": {"drive": {"root_folder_id": "curto"}},
            }
        ]
        client = _client(rows)
        res = client.get(
            "/workspaces/atabaque/workflows/rights_clearance/drive-config",
            headers=ADMIN_HEADERS,
        )
        self.assertTrue(any("root_folder_id" in w for w in res.json()["warnings"]))


class DriveConfigPatchTests(unittest.TestCase):
    def test_patch_persists_subfolders_and_overrides(self) -> None:
        rows: list[dict] = []
        client = _client(rows)
        res = client.patch(
            "/workspaces/atabaque/workflows/rights_clearance/drive-config",
            headers=ADMIN_HEADERS,
            json={
                "subfolders": ["01 Documentos", "02 Materiais"],
                "overrides": {"clearance_musical_root_override": "1XyZAbCdEfGhIjK"},
            },
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["subfolders"], ["01 Documentos", "02 Materiais"])
        self.assertEqual(body["overrides"]["clearance_musical_root_override"], "1XyZAbCdEfGhIjK")
        self.assertIn("subfolders", body["updated"])

        # persistiu de fato — novo GET lê da "base"
        res2 = client.get(
            "/workspaces/atabaque/workflows/rights_clearance/drive-config",
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(res2.json()["_origin"], "db")
        self.assertEqual(res2.json()["subfolders"], ["01 Documentos", "02 Materiais"])

    def test_patch_preserves_existing_extra_settings_blocks(self) -> None:
        rows = [
            {
                "workspace_slug": "atabaque",
                "workflow_type": "rights_clearance",
                "airtable_sync_enabled": True,
                "extra_settings": {
                    "airtable": {"base_id_override": "appX123"},
                    "drive": {"clearance_nonmusical_root_override": "1QwErTyUiOpAsDf"},
                },
            }
        ]
        client = _client(rows)
        res = client.patch(
            "/workspaces/atabaque/workflows/rights_clearance/drive-config",
            headers=ADMIN_HEADERS,
            json={"subfolders": ["Docs"]},
        )
        self.assertEqual(res.status_code, 200)
        stored = rows[0]["extra_settings"]
        self.assertEqual(stored["airtable"]["base_id_override"], "appX123")
        self.assertEqual(stored["drive"]["clearance_nonmusical_root_override"], "1QwErTyUiOpAsDf")
        self.assertEqual(stored["drive"]["subfolders"], ["Docs"])

    def test_patch_ignores_underscore_keys_from_panel(self) -> None:
        rows: list[dict] = []
        client = _client(rows)
        res = client.patch(
            "/workspaces/atabaque/workflows/release_intake/drive-config",
            headers=ADMIN_HEADERS,
            json={"overrides": {"_service": "hack", "root_folder_id": "1LongEnoughFolderId"}},
        )
        body = res.json()
        self.assertNotIn("_service", body["overrides"])
        self.assertEqual(body["overrides"]["root_folder_id"], "1LongEnoughFolderId")

    def test_patch_empty_body_is_noop(self) -> None:
        rows: list[dict] = []
        client = _client(rows)
        res = client.patch(
            "/workspaces/atabaque/workflows/rights_clearance/drive-config",
            headers=ADMIN_HEADERS,
            json={},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
