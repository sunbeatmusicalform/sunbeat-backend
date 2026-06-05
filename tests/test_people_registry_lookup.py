from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import people_registry as people_registry_service


class _LookupResult:
    data = [
        {
            "id": "real-record-id",
            "workspace_slug": "atabaque",
            "display_name": "Ana Teste",
            "roles_json": ["artista", "compositor"],
            "document_id": "00000000000",
            "email_primary": "never@example.com",
            "edit_token": "secret-token",
            "payload": {"secret": True},
            "airtable_record_id": "recSensitive",
        },
        {
            "id": "other-record-id",
            "workspace_slug": "atabaque",
            "display_name": "Ana Editora",
            "roles_json": ["editora"],
        },
    ]


class _LookupQuery:
    def __init__(self) -> None:
        self.selected: str | None = None

    def select(self, columns: str):
        self.selected = columns
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def ilike(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        assert self.selected == "id, workspace_slug, display_name, roles_json"
        return _LookupResult()


class _LookupSupabase:
    def table(self, name: str):
        assert name == "people_registry_records"
        return _LookupQuery()


class PeopleRegistryLookupTests(unittest.TestCase):
    def test_short_query_returns_empty_without_querying_database(self) -> None:
        with patch.object(people_registry_service, "supabase") as supabase_mock:
            response = people_registry_service.lookup_people_registry_records(
                workspace_slug="atabaque",
                query="a",
                roles="artista",
                limit=5,
            )

        self.assertEqual(response.model_dump(mode="json"), {"ok": True, "items": []})
        supabase_mock.table.assert_not_called()

    def test_lookup_returns_sanitized_role_filtered_items(self) -> None:
        with patch.object(
            people_registry_service,
            "supabase",
            _LookupSupabase(),
        ):
            response = people_registry_service.lookup_people_registry_records(
                workspace_slug="atabaque",
                query="ana",
                roles="artista",
                limit=5,
            )

        data = response.model_dump(mode="json")
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]

        self.assertEqual(item["displayName"], "Ana Teste")
        self.assertEqual(item["roles"], ["artista", "compositor"])
        self.assertEqual(item["source"], "people_registry")
        self.assertEqual(item["confidence"], "partial")
        self.assertTrue(item["id"].startswith("people_lookup_"))

        serialized = str(data)
        for forbidden in [
            "real-record-id",
            "00000000000",
            "never@example.com",
            "secret-token",
            "recSensitive",
            "payload",
        ]:
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
