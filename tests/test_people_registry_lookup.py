from __future__ import annotations

import unittest
import sys
import types
from unittest.mock import patch


class _SchemaModel:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)

    @classmethod
    def model_validate(cls, payload):
        return cls(**dict(payload or {}))

    def model_dump(self, mode=None):
        def dump(value):
            if hasattr(value, "model_dump"):
                return value.model_dump(mode=mode)
            if isinstance(value, list):
                return [dump(item) for item in value]
            if isinstance(value, dict):
                return {key: dump(item) for key, item in value.items()}
            return value

        return {key: dump(value) for key, value in self.__dict__.items()}


_schema_module = types.ModuleType("app.schemas.people_registry")
for _schema_name in [
    "PeopleRegistryErrorDetailPayload",
    "PeopleRegistryLookupItemPayload",
    "PeopleRegistryLookupResponsePayload",
    "PeopleRegistryPayload",
    "PeopleRegistryPreparedPayload",
    "PeopleRegistryRecordPayload",
    "PeopleRegistryResponsePayload",
    "PeopleRegistryValidationIssuePayload",
]:
    setattr(_schema_module, _schema_name, _SchemaModel)

_workspace_config_module = types.ModuleType("app.services.workspace_config")
_workspace_config_module.get_workflow_settings = lambda *_args, **_kwargs: {}
_workspace_config_module.get_email_extra_config = lambda *_args, **_kwargs: {}
_workspace_config_module.get_email_event_config = lambda *_args, **_kwargs: {}
_workspace_config_module.get_email_template_config = lambda *_args, **_kwargs: {}
_workspace_config_module.is_email_event_enabled = lambda *_args, **_kwargs: False

_airtable_sync_module = types.ModuleType("app.services.people_registry_airtable_sync")
_airtable_sync_module.sync_people_registry_record_to_airtable = (
    lambda *_args, **_kwargs: None
)

sys.modules.setdefault("app.core.database", types.SimpleNamespace(supabase=object()))
sys.modules.setdefault("app.schemas.people_registry", _schema_module)
sys.modules.setdefault("app.services.workspace_config", _workspace_config_module)
sys.modules.setdefault(
    "app.services.people_registry_airtable_sync",
    _airtable_sync_module,
)

from app.services import people_registry as people_registry_service


class _LookupResult:
    def __init__(self, rows):
        self.data = rows


class _LookupQuery:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.selected: str | None = None
        self.limit_value: int | None = None

    def select(self, columns: str):
        self.selected = columns
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def ilike(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value, *_args, **_kwargs):
        self.limit_value = value
        return self

    def execute(self):
        assert self.selected == "id, workspace_slug, display_name, roles_json"
        assert self.limit_value == people_registry_service.PEOPLE_LOOKUP_CANDIDATE_LIMIT
        return _LookupResult(self.rows)


class _LookupSupabase:
    def __init__(self, rows) -> None:
        self.rows = rows

    def table(self, name: str):
        assert name == "people_registry_records"
        return _LookupQuery(self.rows)


SENSITIVE_LOOKUP_ROWS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "workspace_slug": "atabaque",
        "display_name": "Ana Teste",
        "roles_json": ["artista", "compositor"],
        "document_id": "00000000000",
        "email_primary": "never@example.com",
        "phone_primary": "+5511999999999",
        "edit_token": "secret-token",
        "payload": {"secret": True},
        "airtable_record_id": "recSensitive",
    },
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "workspace_slug": "atabaque",
        "display_name": "Ana Editora",
        "roles_json": ["editora"],
    },
]


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
            _LookupSupabase(SENSITIVE_LOOKUP_ROWS),
        ), patch.object(people_registry_service, "_airtable_client_lookup_rows", return_value=[]):
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
        self.assertNotEqual(item["id"], "11111111-1111-1111-1111-111111111111")

        serialized = str(data)
        for forbidden in [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "00000000000",
            "never@example.com",
            "+5511999999999",
            "secret-token",
            "recSensitive",
            "payload",
        ]:
            self.assertNotIn(forbidden, serialized)

    def test_requested_limit_is_capped_to_lookup_maximum(self) -> None:
        rows = [
            {
                "id": f"record-{index}",
                "workspace_slug": "atabaque",
                "display_name": f"Ana Teste {index:02d}",
                "roles_json": ["artista"],
            }
            for index in range(12)
        ]

        with patch.object(
            people_registry_service,
            "supabase",
            _LookupSupabase(rows),
        ), patch.object(people_registry_service, "_airtable_client_lookup_rows", return_value=[]):
            response = people_registry_service.lookup_people_registry_records(
                workspace_slug="atabaque",
                query="ana",
                roles="artista",
                limit=99,
            )

        data = response.model_dump(mode="json")
        self.assertEqual(
            len(data["items"]),
            people_registry_service.PEOPLE_LOOKUP_MAX_LIMIT,
        )
        self.assertTrue(
            all(item["id"].startswith("people_lookup_") for item in data["items"])
        )

    def test_lookup_is_accent_and_typo_tolerant_and_ranks_client_candidates(self) -> None:
        client_rows = [
            {
                "id": "rec-ludmilla",
                "workspace_slug": "atabaque",
                "display_name": "Ludmilla",
                "roles_json": ["artista"],
            },
            {
                "id": "rec-luciana",
                "workspace_slug": "atabaque",
                "display_name": "Luciana",
                "roles_json": ["artista"],
            },
        ]
        with patch.object(
            people_registry_service,
            "supabase",
            _LookupSupabase([]),
        ), patch.object(
            people_registry_service,
            "_airtable_client_lookup_rows",
            return_value=client_rows,
        ):
            response = people_registry_service.lookup_people_registry_records(
                workspace_slug="atabaque",
                query="Ludmila",
                roles="artista",
                limit=5,
            )

        items = response.model_dump(mode="json")["items"]
        self.assertEqual(items[0]["displayName"], "Ludmilla")
        self.assertEqual(items[0]["confidence"], "partial")


if __name__ == "__main__":
    unittest.main()
