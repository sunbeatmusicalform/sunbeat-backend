from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

sys.modules.setdefault("app.core.database", types.SimpleNamespace(supabase=object()))

from app.services import release_intake_history as history_service


class _LookupResult:
    def __init__(self, rows):
        self.data = rows


class _LookupQuery:
    def __init__(self, rows, table_name, calls) -> None:
        self.rows = rows
        self.table_name = table_name
        self.calls = calls
        self.selected = None
        self.filters = []
        self.limit_value = None
        self.order_field = None
        self.order_desc = False

    def select(self, columns: str):
        self.selected = columns
        return self

    def eq(self, field: str, value):
        self.filters.append((field, value))
        return self

    def order(self, field: str, desc: bool = False):
        self.order_field = field
        self.order_desc = desc
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        rows = list(self.rows)
        for field, value in self.filters:
            rows = [row for row in rows if row.get(field) == value]

        if self.order_field:
            rows = sorted(
                rows,
                key=lambda row: str(row.get(self.order_field) or ""),
                reverse=self.order_desc,
            )

        if self.limit_value is not None:
            rows = rows[: self.limit_value]

        self.calls.append(
            {
                "table": self.table_name,
                "selected": self.selected,
                "filters": list(self.filters),
                "limit": self.limit_value,
            }
        )
        return _LookupResult(rows)


class _LookupSupabase:
    def __init__(self, rows_by_table) -> None:
        self.rows_by_table = rows_by_table
        self.calls = []

    def table(self, name: str):
        return _LookupQuery(self.rows_by_table.get(name, []), name, self.calls)


DRAFT_ROWS = [
    {
        "draft_token": "draft-good",
        "client_slug": "atabaque",
        "submitter_email": "submitter@example.com",
        "meta": {"workflow_type": "release_intake"},
    },
    {
        "draft_token": "draft-other-workspace",
        "client_slug": "other",
        "submitter_email": "submitter@example.com",
        "meta": {"workflow_type": "release_intake"},
    },
]


SUBMISSION_ROWS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "client_slug": "atabaque",
        "email": "submitter@example.com",
        "created_at": "2026-05-01T00:00:00+00:00",
        "updated_at": "2026-06-01T00:00:00+00:00",
        "submitted_at": "2026-06-01T00:00:00+00:00",
        "payload": {
            "workspace_slug": "atabaque",
            "workflow_type": "release_intake",
            "identification": {
                "submitter_email": "submitter@example.com",
                "submitter_name": "Sensitive Name",
            },
            "project": {
                "presskit_link": "https://press.example/kit",
                "promo_assets_link": "https://assets.example/folder",
            },
            "tracks": [
                {
                    "primary_artists": "Ana Teste, Bruno Teste",
                    "authors": "Ana Autora",
                    "phonographic_producer": "Produtora Azul",
                    "existing_profile_links": "https://open.spotify.com/artist/abc",
                },
                {"primary_artists": "Ana Teste"},
            ],
            "secret": "payload-must-not-leak",
        },
        "edit_token": "must-not-leak",
        "draft_token": "must-not-leak",
        "airtable_project_id": "recSensitive",
    },
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "client_slug": "atabaque",
        "email": "other@example.com",
        "updated_at": "2026-06-02T00:00:00+00:00",
        "payload": {
            "workspace_slug": "atabaque",
            "workflow_type": "release_intake",
            "tracks": [{"primary_artists": "Ana Other"}],
        },
    },
    {
        "id": "33333333-3333-3333-3333-333333333333",
        "client_slug": "atabaque",
        "email": "submitter@example.com",
        "updated_at": "2026-06-03T00:00:00+00:00",
        "payload": {
            "workspace_slug": "atabaque",
            "workflow_type": "rights_clearance",
            "tracks": [{"primary_artists": "Ana Clearance"}],
        },
    },
]


class ReleaseIntakeHistoryLookupTests(unittest.TestCase):
    def test_short_query_returns_empty_without_querying_database(self) -> None:
        with patch.object(history_service, "supabase") as supabase_mock:
            response = history_service.lookup_release_intake_submitter_history(
                workspace_slug="atabaque",
                field="primary_artists",
                query="a",
                limit=5,
                draft_token="draft-good",
            )

        self.assertEqual(response, {"ok": True, "items": []})
        supabase_mock.table.assert_not_called()

    def test_disallowed_field_returns_empty_without_querying_database(self) -> None:
        with patch.object(history_service, "supabase") as supabase_mock:
            response = history_service.lookup_release_intake_submitter_history(
                workspace_slug="atabaque",
                field="submitter_email",
                query="ana",
                limit=5,
                draft_token="draft-good",
            )

        self.assertEqual(response, {"ok": True, "items": []})
        supabase_mock.table.assert_not_called()

    def test_invalid_token_returns_empty_without_history_query(self) -> None:
        fake_supabase = _LookupSupabase(
            {
                history_service.RELEASE_INTAKE_DRAFTS_TABLE: DRAFT_ROWS,
                history_service.SUBMISSIONS_TABLE: SUBMISSION_ROWS,
            }
        )

        with patch.object(history_service, "supabase", fake_supabase):
            response = history_service.lookup_release_intake_submitter_history(
                workspace_slug="atabaque",
                field="primary_artists",
                query="ana",
                limit=5,
                draft_token="missing-token",
            )

        self.assertEqual(response, {"ok": True, "items": []})
        self.assertEqual(
            [call["table"] for call in fake_supabase.calls],
            [history_service.RELEASE_INTAKE_DRAFTS_TABLE],
        )

    def test_workspace_mismatch_returns_empty(self) -> None:
        fake_supabase = _LookupSupabase(
            {
                history_service.RELEASE_INTAKE_DRAFTS_TABLE: DRAFT_ROWS,
                history_service.SUBMISSIONS_TABLE: SUBMISSION_ROWS,
            }
        )

        with patch.object(history_service, "supabase", fake_supabase):
            response = history_service.lookup_release_intake_submitter_history(
                workspace_slug="atabaque",
                field="primary_artists",
                query="ana",
                limit=5,
                draft_token="draft-other-workspace",
            )

        self.assertEqual(response, {"ok": True, "items": []})
        self.assertEqual(
            [call["table"] for call in fake_supabase.calls],
            [history_service.RELEASE_INTAKE_DRAFTS_TABLE],
        )

    def test_lookup_returns_sanitized_same_submitter_history(self) -> None:
        fake_supabase = _LookupSupabase(
            {
                history_service.RELEASE_INTAKE_DRAFTS_TABLE: DRAFT_ROWS,
                history_service.SUBMISSIONS_TABLE: SUBMISSION_ROWS,
            }
        )

        with patch.object(history_service, "supabase", fake_supabase):
            response = history_service.lookup_release_intake_submitter_history(
                workspace_slug="atabaque",
                field="primary_artists",
                query="ana",
                limit=5,
                draft_token="draft-good",
            )

        self.assertEqual(response["ok"], True)
        self.assertEqual(len(response["items"]), 1)
        item = response["items"][0]
        self.assertEqual(
            item,
            {
                "value": "Ana Teste",
                "field": "primary_artists",
                "source": "submitter_history",
                "count": 2,
                "lastUsedAt": "2026-06-01T00:00:00+00:00",
            },
        )

        serialized = str(response)
        for forbidden in [
            "submitter@example.com",
            "other@example.com",
            "Sensitive Name",
            "payload-must-not-leak",
            "must-not-leak",
            "recSensitive",
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "Ana Other",
            "Ana Clearance",
        ]:
            self.assertNotIn(forbidden, serialized)

    def test_edit_token_can_resolve_bound_submitter(self) -> None:
        edit_rows = [
            dict(SUBMISSION_ROWS[0], edit_token="edit-good"),
        ]
        fake_supabase = _LookupSupabase(
            {
                history_service.RELEASE_INTAKE_DRAFTS_TABLE: DRAFT_ROWS,
                history_service.SUBMISSIONS_TABLE: edit_rows + SUBMISSION_ROWS,
            }
        )

        with patch.object(history_service, "supabase", fake_supabase):
            response = history_service.lookup_release_intake_submitter_history(
                workspace_slug="atabaque",
                field="presskit_link",
                query="press",
                limit=5,
                edit_token="edit-good",
            )

        self.assertEqual(
            response,
            {
                "ok": True,
                "items": [
                    {
                        "value": "https://press.example/kit",
                        "field": "presskit_link",
                        "source": "submitter_history",
                        "count": 2,
                        "lastUsedAt": "2026-06-01T00:00:00+00:00",
                    }
                ],
            },
        )

    def test_requested_limit_is_capped(self) -> None:
        rows = []
        for index in range(12):
            rows.append(
                {
                    "client_slug": "atabaque",
                    "email": "submitter@example.com",
                    "updated_at": f"2026-06-{index + 1:02d}T00:00:00+00:00",
                    "payload": {
                        "workflow_type": "release_intake",
                        "tracks": [
                            {"authors": f"Ana Autora {index:02d}"},
                        ],
                    },
                }
            )

        fake_supabase = _LookupSupabase(
            {
                history_service.RELEASE_INTAKE_DRAFTS_TABLE: DRAFT_ROWS,
                history_service.SUBMISSIONS_TABLE: rows,
            }
        )

        with patch.object(history_service, "supabase", fake_supabase):
            response = history_service.lookup_release_intake_submitter_history(
                workspace_slug="atabaque",
                field="authors",
                query="ana",
                limit=99,
                draft_token="draft-good",
            )

        self.assertEqual(
            len(response["items"]),
            history_service.HISTORY_LOOKUP_MAX_LIMIT,
        )


if __name__ == "__main__":
    unittest.main()
