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

try:
    import supabase  # noqa: F401
except ModuleNotFoundError:
    supabase_stub = types.ModuleType("supabase")
    supabase_stub.create_client = lambda *_args, **_kwargs: object()
    sys.modules["supabase"] = supabase_stub

from app.services import workspace_config


class _FakeTable:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = deepcopy(rows)
        self.filters: list[tuple[str, object]] = []

    def select(self, _fields: str) -> "_FakeTable":
        return self

    def eq(self, key: str, value: object) -> "_FakeTable":
        self.filters.append((key, value))
        return self

    def limit(self, _count: int) -> "_FakeTable":
        return self

    def execute(self) -> SimpleNamespace:
        matched = [
            deepcopy(row)
            for row in self.rows
            if all(row.get(key) == value for key, value in self.filters)
        ]
        return SimpleNamespace(data=matched)


class _FakeSupabase:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def table(self, name: str) -> _FakeTable:
        if name != "workspace_workflow_settings":
            raise AssertionError(f"Unexpected table: {name}")
        return _FakeTable(self.rows)


class WorkspaceConfigOperationalBasesTests(unittest.TestCase):
    def test_company_registry_defaults_include_operational_base_metadata(self) -> None:
        with patch.object(workspace_config, "supabase", _FakeSupabase([])):
            settings = workspace_config.get_workflow_settings(
                "atabaque",
                "company_registry",
            )

        extra = settings["extra_settings"]
        self.assertEqual(
            extra["operational_base"]["tables"],
            ["submissions", "submissions_revisions"],
        )
        self.assertEqual(
            extra["airtable"]["_service"],
            "app.services.airtable_company_registry",
        )
        self.assertIn("company_registry_table_override", extra["airtable"])
        self.assertEqual(extra["drive"]["_service"], None)

    def test_partial_db_extra_settings_inherits_operational_defaults(self) -> None:
        rows = [
            {
                "workspace_slug": "atabaque",
                "workflow_type": "people_registry",
                "post_submit_email_enabled": None,
                "edit_email_enabled": None,
                "airtable_sync_enabled": None,
                "drive_sync_enabled": None,
                "edit_mode_enabled": None,
                "extra_settings": {
                    "airtable": {
                        "base_id_override": "appOverride",
                    }
                },
            }
        ]

        with patch.object(workspace_config, "supabase", _FakeSupabase(rows)):
            settings = workspace_config.get_workflow_settings(
                "atabaque",
                "people_registry",
            )

        airtable = settings["extra_settings"]["airtable"]
        self.assertEqual(airtable["base_id_override"], "appOverride")
        self.assertIn("people_registry_table_override", airtable)
        self.assertEqual(
            settings["extra_settings"]["operational_base"]["tables"],
            ["people_registry_records"],
        )

    def test_operational_base_helper_returns_a_deep_copy(self) -> None:
        first = workspace_config.get_workflow_operational_base("release_intake")
        first["airtable"]["_settings_keys"].append("MUTATED")

        second = workspace_config.get_workflow_operational_base("release_intake")
        self.assertNotIn("MUTATED", second["airtable"]["_settings_keys"])


if __name__ == "__main__":
    unittest.main()
