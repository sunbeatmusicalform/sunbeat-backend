from __future__ import annotations

import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")

from app.services import airtable as airtable_module


class _FakeTable:
    def __init__(self, store: dict[str, list[dict]], name: str) -> None:
        self.store = store
        self.name = name
        self._mode: str | None = None
        self._payload: dict | None = None
        self._filters: list[tuple[str, object]] = []

    def update(self, payload: dict) -> "_FakeTable":
        self._mode = "update"
        self._payload = payload
        return self

    def eq(self, key: str, value: object) -> "_FakeTable":
        self._filters.append((key, value))
        return self

    def execute(self) -> SimpleNamespace:
        try:
            if self._mode != "update":
                raise AssertionError(f"Unsupported mode for fake Airtable table: {self._mode}")

            updated: list[dict] = []
            for row in self.store.setdefault(self.name, []):
                if all(row.get(key) == value for key, value in self._filters):
                    row.update(deepcopy(self._payload or {}))
                    updated.append(deepcopy(row))
            return SimpleNamespace(data=updated)
        finally:
            self._mode = None
            self._payload = None
            self._filters = []


class _FakeSupabase:
    def __init__(self, store: dict[str, list[dict]]) -> None:
        self.store = deepcopy(store)

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self.store, name)


def _submission(*, airtable_project_id: str | None = None) -> dict:
    return {
        "id": "sub-123",
        "client_slug": "atabaque",
        "airtable_project_id": airtable_project_id,
        "payload": {
            "workspace_slug": "atabaque",
            "identification": {
                "submitter_name": "Ana",
                "submitter_email": "ana@example.com",
                "project_title": "Projeto Teste",
                "release_type": "single",
            },
            "project": {
                "release_date": "2026-05-01",
                "genre": "pop",
            },
            "marketing": {
                "marketing_focus": "streaming",
            },
        },
    }


class AirtableUpsertTests(unittest.TestCase):
    def test_focus_track_uses_text_for_current_projects_schema(self) -> None:
        with (
            patch.object(airtable_module, "_table_url", return_value="https://airtable/projects"),
            patch.object(airtable_module, "_request_json", return_value={"id": "recProject"}) as request,
        ):
            airtable_module.update_airtable_project_focus_track(
                airtable_project_id="recProject",
                airtable_focus_track_id="recTrack",
                focus_track_name="Faixa A",
            )

        self.assertEqual(
            request.call_args.args[2],
            {"fields": {"Faixa Foco": "Faixa A"}},
        )

    def test_focus_track_falls_back_to_linked_record_for_legacy_schema(self) -> None:
        with (
            patch.object(airtable_module, "_table_url", return_value="https://airtable/projects"),
            patch.object(
                airtable_module,
                "_request_json",
                side_effect=[RuntimeError("INVALID_VALUE_FOR_COLUMN"), {"id": "recProject"}],
            ) as request,
        ):
            airtable_module.update_airtable_project_focus_track(
                airtable_project_id="recProject",
                airtable_focus_track_id="recTrack",
                focus_track_name="Faixa A",
            )

        self.assertEqual(
            request.call_args_list[1].args[2],
            {"fields": {"Faixa Foco": ["recTrack"]}},
        )

    def test_upsert_airtable_project_uses_patch_when_record_exists(self) -> None:
        request_calls: list[dict] = []

        def request_side_effect(method: str, url: str, payload: dict | None = None, params: dict | None = None) -> dict:
            request_calls.append(
                {
                    "method": method,
                    "url": url,
                    "payload": deepcopy(payload),
                    "params": deepcopy(params),
                }
            )
            return {"id": "recProjectExisting", "fields": {}}

        with (
            patch.object(airtable_module, "_request_json", side_effect=request_side_effect),
            patch.object(airtable_module, "_table_url", return_value="https://airtable/projects"),
        ):
            result = airtable_module.upsert_airtable_project(
                _submission(airtable_project_id="recProjectExisting")
            )

        self.assertEqual(result["id"], "recProjectExisting")
        self.assertEqual(len(request_calls), 1)
        self.assertEqual(request_calls[0]["method"], "PATCH")
        self.assertTrue(request_calls[0]["url"].endswith("/recProjectExisting"))

    def test_upsert_airtable_project_posts_and_persists_id_when_missing(self) -> None:
        fake_supabase = _FakeSupabase({"submissions": [{"id": "sub-123"}]})
        request_calls: list[dict] = []

        def request_side_effect(method: str, url: str, payload: dict | None = None, params: dict | None = None) -> dict:
            request_calls.append(
                {
                    "method": method,
                    "url": url,
                    "payload": deepcopy(payload),
                }
            )
            return {"records": [{"id": "recProjectNew", "fields": {}}]}

        submission = _submission()

        with (
            patch.object(airtable_module, "supabase", fake_supabase),
            patch.object(airtable_module, "_request_json", side_effect=request_side_effect),
            patch.object(airtable_module, "_table_url", return_value="https://airtable/projects"),
        ):
            result = airtable_module.upsert_airtable_project(submission)

        self.assertEqual(result["id"], "recProjectNew")
        self.assertEqual(request_calls[0]["method"], "POST")
        self.assertEqual(
            fake_supabase.store["submissions"][0]["airtable_project_id"],
            "recProjectNew",
        )
        self.assertEqual(submission["airtable_project_id"], "recProjectNew")

    def test_upsert_airtable_tracks_posts_new_patches_existing_and_marks_removed(self) -> None:
        fake_supabase = _FakeSupabase(
            {
                "tracks": [
                    {"id": "track-existing", "airtable_track_id": "recTrackExisting"},
                    {"id": "track-new", "airtable_track_id": None},
                    {"id": "track-removed", "airtable_track_id": "recTrackRemoved"},
                ]
            }
        )
        request_calls: list[dict] = []

        def request_side_effect(method: str, url: str, payload: dict | None = None, params: dict | None = None) -> dict:
            request_calls.append(
                {
                    "method": method,
                    "url": url,
                    "payload": deepcopy(payload),
                    "params": deepcopy(params),
                }
            )
            if method == "POST":
                return {
                    "records": [
                        {
                            "id": "recTrackNew",
                            "fields": {
                                "Projeto": ["recProject"],
                                "Ordem da Faixa": 2,
                            },
                        }
                    ]
                }
            if url.endswith("/recTrackExisting"):
                return {"id": "recTrackExisting", "fields": {"Ordem da Faixa": 1}}
            if url.endswith("/recTrackRemoved"):
                return {"id": "recTrackRemoved", "fields": {"Status da Faixa": "Removida"}}
            raise AssertionError(f"Unexpected Airtable request: {method} {url}")

        submission = _submission(airtable_project_id="recProject")
        tracks = [
            {
                "id": "track-existing",
                "client_track_id": "ct-existing",
                "airtable_track_id": "recTrackExisting",
                "deleted_at": None,
                "order_number": 1,
                "title": "Faixa A",
                "artists": "Ana",
                "authors": "Ana",
                "lyrics": "Letra A",
            },
            {
                "id": "track-new",
                "client_track_id": "ct-new",
                "airtable_track_id": None,
                "deleted_at": None,
                "order_number": 2,
                "title": "Faixa B",
                "artists": "Ana",
                "authors": "Ana",
                "lyrics": "Letra B",
            },
            {
                "id": "track-removed",
                "client_track_id": "ct-removed",
                "airtable_track_id": "recTrackRemoved",
                "deleted_at": "2026-04-18T12:00:00+00:00",
                "order_number": 3,
                "title": "Faixa C",
                "artists": "Ana",
                "authors": "Ana",
                "lyrics": "Letra C",
            },
        ]

        with (
            patch.object(airtable_module, "supabase", fake_supabase),
            patch.object(airtable_module, "_request_json", side_effect=request_side_effect),
            patch.object(
                airtable_module,
                "_table_url",
                side_effect=lambda table_name, _base_id=None: f"https://airtable/{table_name}",
            ),
            patch.object(airtable_module, "_tracks_table_name", return_value="tracks"),
            patch.object(airtable_module, "_track_project_link_field", return_value="Projeto"),
            patch.object(airtable_module, "_track_status_field", return_value="Status da Faixa"),
        ):
            result = airtable_module.upsert_airtable_tracks(submission, tracks)

        methods = [call["method"] for call in request_calls]
        self.assertEqual(methods.count("POST"), 1)
        # PATCHes: 1 canonical on recTrackExisting, 1 Ativa on recTrackExisting,
        # 1 Removida on recTrackRemoved.
        self.assertEqual(methods.count("PATCH"), 3)
        self.assertEqual(fake_supabase.store["tracks"][1]["airtable_track_id"], "recTrackNew")
        self.assertEqual(len(result), 3)

        removed_call = next(call for call in request_calls if call["url"].endswith("/recTrackRemoved"))
        self.assertEqual(
            removed_call["payload"]["fields"]["Status da Faixa"],
            "Removida",
        )

        # Ativa must be stamped on the active, already-linked track (covers
        # reactivation idempotently).
        existing_calls = [
            call for call in request_calls if call["url"].endswith("/recTrackExisting")
        ]
        self.assertEqual(len(existing_calls), 2)
        self.assertEqual(
            existing_calls[1]["payload"]["fields"]["Status da Faixa"],
            "Ativa",
        )

    def test_upsert_airtable_tracks_tolerates_missing_status_field(self) -> None:
        """When the 'Status da Faixa' field is absent from the Airtable base,
        the sync must not fail: canonical PATCH still runs, and the status
        PATCH is skipped with a warning."""

        fake_supabase = _FakeSupabase(
            {
                "tracks": [
                    {"id": "track-existing", "airtable_track_id": "recTrackExisting"},
                    {"id": "track-removed", "airtable_track_id": "recTrackRemoved"},
                ]
            }
        )
        request_calls: list[dict] = []

        unknown_field_error = RuntimeError(
            "Airtable HTTP 422: {'error': {'type': 'UNKNOWN_FIELD_NAME', "
            "'message': \"Unknown field name: 'Status da Faixa'\"}}"
        )

        def request_side_effect(method: str, url: str, payload: dict | None = None, params: dict | None = None) -> dict:
            request_calls.append(
                {
                    "method": method,
                    "url": url,
                    "payload": deepcopy(payload),
                    "params": deepcopy(params),
                }
            )
            fields = (payload or {}).get("fields", {}) or {}
            # Simulate the Airtable base without the status field: any PATCH
            # that targets ONLY the status field fails with UNKNOWN_FIELD_NAME.
            if list(fields.keys()) == ["Status da Faixa"]:
                raise unknown_field_error
            if url.endswith("/recTrackExisting"):
                return {"id": "recTrackExisting", "fields": {"Ordem da Faixa": 1}}
            raise AssertionError(f"Unexpected Airtable request: {method} {url}")

        submission = _submission(airtable_project_id="recProject")
        tracks = [
            {
                "id": "track-existing",
                "client_track_id": "ct-existing",
                "airtable_track_id": "recTrackExisting",
                "deleted_at": None,
                "order_number": 1,
                "title": "Faixa A",
                "artists": "Ana",
                "authors": "Ana",
                "lyrics": "Letra A",
            },
            {
                "id": "track-removed",
                "client_track_id": "ct-removed",
                "airtable_track_id": "recTrackRemoved",
                "deleted_at": "2026-04-18T12:00:00+00:00",
                "order_number": 2,
                "title": "Faixa B",
                "artists": "Ana",
                "authors": "Ana",
                "lyrics": "Letra B",
            },
        ]

        with (
            patch.object(airtable_module, "supabase", fake_supabase),
            patch.object(airtable_module, "_request_json", side_effect=request_side_effect),
            patch.object(
                airtable_module,
                "_table_url",
                side_effect=lambda table_name, _base_id=None: f"https://airtable/{table_name}",
            ),
            patch.object(airtable_module, "_tracks_table_name", return_value="tracks"),
            patch.object(airtable_module, "_track_project_link_field", return_value="Projeto"),
            patch.object(airtable_module, "_track_status_field", return_value="Status da Faixa"),
        ):
            # Must not raise even though the status field is missing.
            result = airtable_module.upsert_airtable_tracks(submission, tracks)

        methods = [call["method"] for call in request_calls]
        # 1 canonical PATCH on recTrackExisting, 1 status PATCH that fails,
        # 1 status PATCH on recTrackRemoved that fails.
        self.assertEqual(methods.count("PATCH"), 3)
        self.assertEqual(len(result), 2)

        # The canonical fields PATCH for the active track must have landed
        # regardless of the status field being absent.
        canonical_existing = [
            call
            for call in request_calls
            if call["url"].endswith("/recTrackExisting")
            and "Ordem da Faixa" in (call["payload"] or {}).get("fields", {})
        ]
        self.assertEqual(len(canonical_existing), 1)

        # The removed track result entry keeps the airtable id and carries the
        # deleted_at marker even though Airtable could not reflect the status.
        removed_entry = next(
            entry for entry in result if entry["submission_track_id"] == "track-removed"
        )
        self.assertEqual(removed_entry["id"], "recTrackRemoved")
        self.assertEqual(
            removed_entry["deleted_at"], "2026-04-18T12:00:00+00:00"
        )

    def test_upsert_airtable_tracks_forces_ativa_on_reactivation(self) -> None:
        """A track whose deleted_at transitions from set to None on edit must
        receive a PATCH of Status da Faixa = 'Ativa' so Airtable reflects the
        reactivation without waiting for manual review."""

        fake_supabase = _FakeSupabase(
            {
                "tracks": [
                    {
                        "id": "track-reactivated",
                        "airtable_track_id": "recTrackReactivated",
                    },
                ]
            }
        )
        request_calls: list[dict] = []

        def request_side_effect(method: str, url: str, payload: dict | None = None, params: dict | None = None) -> dict:
            request_calls.append(
                {
                    "method": method,
                    "url": url,
                    "payload": deepcopy(payload),
                }
            )
            return {"id": "recTrackReactivated", "fields": {"Ordem da Faixa": 1}}

        submission = _submission(airtable_project_id="recProject")
        tracks = [
            {
                "id": "track-reactivated",
                "client_track_id": "ct-reactivated",
                "airtable_track_id": "recTrackReactivated",
                "deleted_at": None,  # reactivated: deleted_at cleared on this edit
                "order_number": 1,
                "title": "Faixa Reativada",
                "artists": "Ana",
                "authors": "Ana",
                "lyrics": "Letra",
            },
        ]

        with (
            patch.object(airtable_module, "supabase", fake_supabase),
            patch.object(airtable_module, "_request_json", side_effect=request_side_effect),
            patch.object(
                airtable_module,
                "_table_url",
                side_effect=lambda table_name, _base_id=None: f"https://airtable/{table_name}",
            ),
            patch.object(airtable_module, "_tracks_table_name", return_value="tracks"),
            patch.object(airtable_module, "_track_project_link_field", return_value="Projeto"),
            patch.object(airtable_module, "_track_status_field", return_value="Status da Faixa"),
        ):
            airtable_module.upsert_airtable_tracks(submission, tracks)

        # Exactly two PATCHes: one canonical, one stamping Ativa.
        self.assertEqual(len(request_calls), 2)
        self.assertEqual(request_calls[0]["method"], "PATCH")
        self.assertIn("Ordem da Faixa", request_calls[0]["payload"]["fields"])
        self.assertEqual(request_calls[1]["method"], "PATCH")
        self.assertEqual(
            request_calls[1]["payload"]["fields"],
            {"Status da Faixa": "Ativa"},
        )


if __name__ == "__main__":
    unittest.main()
