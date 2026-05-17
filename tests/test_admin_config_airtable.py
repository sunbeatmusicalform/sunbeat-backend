from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")

try:
    import supabase  # noqa: F401
except ModuleNotFoundError:
    supabase_stub = types.ModuleType("supabase")
    supabase_stub.create_client = lambda *_args, **_kwargs: object()
    sys.modules["supabase"] = supabase_stub

from fastapi import HTTPException

from app.modules import admin_config


class _FakeTable:
    def __init__(self, owner: "_FakeSupabase") -> None:
        self.owner = owner
        self.filters: list[tuple[str, object]] = []
        self._upsert_payload: dict | None = None
        self._on_conflict: str | None = None

    def select(self, _fields: str) -> "_FakeTable":
        return self

    def eq(self, key: str, value: object) -> "_FakeTable":
        self.filters.append((key, value))
        return self

    def limit(self, _count: int) -> "_FakeTable":
        return self

    def upsert(self, payload: dict, on_conflict: str) -> "_FakeTable":
        self._upsert_payload = deepcopy(payload)
        self._on_conflict = on_conflict
        return self

    def execute(self) -> SimpleNamespace:
        if self._upsert_payload is not None:
            self.owner.upserts.append(
                {
                    "payload": deepcopy(self._upsert_payload),
                    "on_conflict": self._on_conflict,
                }
            )
            return SimpleNamespace(data=[deepcopy(self._upsert_payload)])

        matched = [
            deepcopy(row)
            for row in self.owner.rows
            if all(row.get(key) == value for key, value in self.filters)
        ]
        return SimpleNamespace(data=matched)


class _FakeSupabase:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = deepcopy(rows)
        self.upserts: list[dict] = []

    def table(self, name: str) -> _FakeTable:
        if name != "workspace_workflow_settings":
            raise AssertionError(f"Unexpected table: {name}")
        return _FakeTable(self)


class AdminConfigAirtableTests(unittest.TestCase):
    def test_read_airtable_config_returns_defaults_with_origins(self) -> None:
        with patch.object(admin_config, "supabase", _FakeSupabase([])):
            response = asyncio.run(
                admin_config.get_airtable_config(
                    "atabaque",
                    "company_registry",
                    None,
                )
            )

        airtable = response["airtable"]
        self.assertEqual(
            airtable["effective"]["company_registry_table_override"],
            "[V2] - Empresas",
        )
        self.assertEqual(
            airtable["origins"]["company_registry_table_override"],
            "default",
        )
        self.assertEqual(airtable["raw"], {})

    def test_patch_airtable_deep_merges_and_maps_table_override_alias(self) -> None:
        fake = _FakeSupabase(
            [
                {
                    "workspace_slug": "atabaque",
                    "workflow_type": "people_registry",
                    "airtable_sync_enabled": True,
                    "extra_settings": {
                        "email": {
                            "events": {
                                "on_submit": {
                                    "enabled": True,
                                    "recipients": ["ops@example.com"],
                                }
                            }
                        },
                        "airtable": {
                            "field_map": {
                                "existing": "Existing Field",
                            }
                        },
                    },
                }
            ]
        )
        body = admin_config.AirtablePatch.model_validate(
            {
                "airtable_sync_enabled": False,
                "airtable": {
                    "table_override": "[V2] - Pessoas Sandbox",
                    "field_map": {
                        "new": "New Field",
                    },
                    "merge_keys": [
                        {
                            "source": "party.document_id",
                            "airtable_field": "Documento",
                            "priority": 1,
                        }
                    ],
                },
            }
        )

        with patch.object(admin_config, "supabase", fake):
            response = asyncio.run(
                admin_config.patch_airtable(
                    "atabaque",
                    "people_registry",
                    body,
                    None,
                )
            )

        self.assertTrue(response["ok"])
        self.assertEqual(
            response["table_override_key"],
            "people_registry_table_override",
        )

        upsert = fake.upserts[0]["payload"]
        self.assertIs(upsert["airtable_sync_enabled"], False)

        extra = upsert["extra_settings"]
        self.assertEqual(
            extra["email"]["events"]["on_submit"]["recipients"],
            ["ops@example.com"],
        )
        self.assertNotIn("table_override", extra["airtable"])
        self.assertEqual(
            extra["airtable"]["people_registry_table_override"],
            "[V2] - Pessoas Sandbox",
        )
        self.assertEqual(
            extra["airtable"]["field_map"],
            {
                "existing": "Existing Field",
                "new": "New Field",
            },
        )
        self.assertEqual(
            extra["airtable"]["merge_keys"][0]["airtable_field"],
            "Documento",
        )

    def test_conflicting_table_override_aliases_are_rejected(self) -> None:
        patch_body = admin_config.AirtableExtraPatch.model_validate(
            {
                "table_override": "[V2] - Empresas A",
                "company_registry_table_override": "[V2] - Empresas B",
            }
        )

        with self.assertRaises(HTTPException) as ctx:
            admin_config._normalize_airtable_patch("company_registry", patch_body)

        self.assertEqual(ctx.exception.status_code, 422)

    def test_wrong_workflow_table_key_is_rejected(self) -> None:
        patch_body = admin_config.AirtableExtraPatch.model_validate(
            {
                "company_registry_table_override": "[V2] - Empresas",
            }
        )

        with self.assertRaises(HTTPException) as ctx:
            admin_config._normalize_airtable_patch("people_registry", patch_body)

        self.assertEqual(ctx.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
