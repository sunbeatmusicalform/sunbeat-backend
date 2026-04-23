from __future__ import annotations

import asyncio
import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from fastapi import BackgroundTasks

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")

from app.modules import submissions as submissions_module
from app.schemas.submission import validate_submission_payload


DRAFT_TOKEN = "6c8b7993-8763-4ee6-8d48-0eb677b98963"
SUBMISSION_ID = "8c1cc4fd-05ea-4d0d-a2cf-a5d277af1f2c"
CLIENT_TRACK_ID_1 = "917405ef-8bcb-4d83-8e46-96b24f0a0abc"
CLIENT_TRACK_ID_2 = "15bf1a18-f048-4de9-a37e-f4c46b7e0423"
CLIENT_TRACK_ID_3 = "4f2c4115-bb44-4a2f-b50d-aa0dfec6035d"


class _FakeTable:
    def __init__(self, store: dict[str, list[dict]], name: str) -> None:
        self.store = store
        self.name = name
        self._mode: str | None = None
        self._payload: dict | list[dict] | None = None
        self._filters: list[tuple[str, object]] = []
        self._limit: int | None = None

    def select(self, *_args: object) -> "_FakeTable":
        self._mode = "select"
        return self

    def insert(self, payload: dict | list[dict]) -> "_FakeTable":
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict) -> "_FakeTable":
        self._mode = "update"
        self._payload = payload
        return self

    def eq(self, key: str, value: object) -> "_FakeTable":
        self._filters.append((key, value))
        return self

    def limit(self, value: int) -> "_FakeTable":
        self._limit = value
        return self

    def execute(self) -> SimpleNamespace:
        rows = self.store.setdefault(self.name, [])

        def matches(row: dict) -> bool:
            return all(row.get(key) == value for key, value in self._filters)

        try:
            if self._mode == "select":
                data = [deepcopy(row) for row in rows if matches(row)]
                if self._limit is not None:
                    data = data[: self._limit]
                return SimpleNamespace(data=data)

            if self._mode == "insert":
                payloads = self._payload if isinstance(self._payload, list) else [self._payload]
                inserted: list[dict] = []
                for payload in payloads:
                    row = deepcopy(payload or {})
                    if "id" not in row:
                        row["id"] = f"{self.name}-{len(rows) + 1}"
                    rows.append(row)
                    inserted.append(deepcopy(row))
                return SimpleNamespace(data=inserted)

            if self._mode == "update":
                updated: list[dict] = []
                for row in rows:
                    if not matches(row):
                        continue
                    row.update(deepcopy(self._payload or {}))
                    updated.append(deepcopy(row))
                return SimpleNamespace(data=updated)

            raise AssertionError(f"Unsupported fake table mode: {self._mode}")
        finally:
            self._mode = None
            self._payload = None
            self._filters = []
            self._limit = None


class _FakeSupabase:
    def __init__(self, initial_store: dict[str, list[dict]] | None = None) -> None:
        self.store = deepcopy(initial_store or {})

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self.store, name)


def _release_payload(
    *,
    edit_token: str | None = None,
    draft_token: str = DRAFT_TOKEN,
    tracks: list[dict] | None = None,
) -> dict:
    return {
        "draft_token": draft_token,
        "edit_token": edit_token,
        "workspace_slug": "atabaque",
        "workflow_type": "release_intake",
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
        "tracks": tracks
        or [
            {
                "local_id": "local-a",
                "order_number": 1,
                "title": "Faixa A",
                "primary_artists": "Ana",
                "authors": "Ana",
                "explicit_content": "no",
                "lyrics": "Letra A",
            },
            {
                "local_id": "local-b",
                "order_number": 2,
                "title": "Faixa B",
                "primary_artists": "Ana",
                "authors": "Ana",
                "explicit_content": "no",
                "lyrics": "Letra B",
            },
        ],
        "marketing": {
            "focus_track_name": "Faixa A",
        },
        "meta": {
            "form_version": "legacy_v1",
        },
    }


def _submission_row(*, payload: dict | None = None, version: int = 1) -> dict:
    normalized_payload = deepcopy(payload or _release_payload())
    return {
        "id": SUBMISSION_ID,
        "draft_token": DRAFT_TOKEN,
        "status": "submitted",
        "created_at": "2026-04-18T11:50:00+00:00",
        "updated_at": "2026-04-18T11:50:00+00:00",
        "submitted_at": "2026-04-18T11:50:00+00:00",
        "version": version,
        "is_update": version > 1,
        "edit_token": "edit-123",
        "client_slug": "atabaque",
        "email": "ana@example.com",
        "artist_name": "Ana",
        "release_type": "single",
        "release_title": "Projeto Teste",
        "main_title": "Projeto Teste",
        "track_title": "Faixa A",
        "genre": "pop",
        "release_date": "2026-05-01",
        "cover_url": None,
        "cover_path": None,
        "marketing_json": deepcopy(normalized_payload.get("marketing") or {}),
        "tracks_json": deepcopy(normalized_payload.get("tracks") or []),
        "payload": deepcopy(normalized_payload),
        "airtable_sync_status": "synced",
        "email_status": "ok",
        "summary_email_sent": True,
        "summary_email_message_id": "msg-1",
    }


def _rights_clearance_payload(*, draft_token: str = DRAFT_TOKEN) -> dict:
    return {
        "draft_token": draft_token,
        "workspace_slug": "atabaque",
        "workflow_type": "rights_clearance",
        "requester_identification": {
            "requester_name": "Ana",
            "requester_email": "ana@example.com",
            "requester_company": "Atabaque",
            "requester_role": "Artist",
        },
        "request_type": {
            "clearance_format": "music_release_clearance_intake",
        },
        "project_context": {
            "project_title": "Projeto Direitos",
            "responsible_company": "Atabaque",
            "client_or_distributor": "Distribuidora X",
            "release_or_start_date": "2026-05-01",
            "release_type": "single",
            "general_clearance_notes": "Precisamos de clearance completo.",
        },
        "tracks": [
            {
                "local_id": "rights-1",
                "order_number": 1,
                "title": "Faixa Direitos",
                "primary_artists": "Ana",
                "authors": "Ana",
                "phonogram_owner": "Atabaque",
                "has_isrc": "no",
            }
        ],
        "meta": {
            "form_version": "legacy_v1",
        },
    }


def _track_row(
    *,
    track_id: str,
    client_track_id: str | None,
    order_number: int,
    title: str,
    deleted_at: str | None = None,
) -> dict:
    return {
        "id": track_id,
        "submission_id": SUBMISSION_ID,
        "draft_token": DRAFT_TOKEN,
        "client_track_id": client_track_id,
        "order_number": order_number,
        "title": title,
        "artists": "Ana",
        "authors": "Ana",
        "lyrics": f"Letra {title}",
        "explicit": False,
        "deleted_at": deleted_at,
        "created_at": "2026-04-18T11:50:00+00:00",
    }


class SubmissionUpsertTests(unittest.TestCase):
    def test_create_submission_persists_revision_and_client_track_ids(self) -> None:
        fake_supabase = _FakeSupabase(
            {
                "submissions": [],
                "tracks": [],
                "submissions_revisions": [],
            }
        )

        with (
            patch.object(submissions_module, "supabase", fake_supabase),
            patch.object(submissions_module, "_generate_edit_token", return_value="edit-new"),
            patch.object(submissions_module, "_utc_now_iso", return_value="2026-04-18T12:00:00+00:00"),
            patch.object(
                submissions_module,
                "_sync_airtable",
                return_value={
                    "airtable_project": {"id": "airtable-project-1"},
                    "airtable_tracks": [],
                    "focus_track_record_id": None,
                },
            ),
            patch.object(submissions_module, "_update_submission_airtable_success"),
            patch.object(submissions_module, "_persist_airtable_track_ids"),
            patch.object(
                submissions_module,
                "_queue_google_drive_sync",
                return_value={"ok": True, "status": "skipped"},
            ),
            patch.object(
                submissions_module,
                "send_edit_link_email",
                return_value={
                    "provider_message_id": "provider-1",
                    "subject": "ok",
                    "edit_url": "https://example.com/edit",
                    "provider_response": {"ok": True},
                    "to_email": "ana@example.com",
                },
            ),
            patch.object(submissions_module, "_update_submission_email_sent"),
            patch.object(submissions_module, "_mark_draft_as_submitted"),
            patch.object(
                submissions_module,
                "_maybe_send_submission_summary_email",
                return_value={"status": "skipped", "recipients_count": 0},
            ),
        ):
            response = submissions_module.create_submission(
                _release_payload(),
                BackgroundTasks(),
                idempotency_key="idem-create-1",
            )

        self.assertTrue(response["ok"])
        self.assertEqual(len(fake_supabase.store["submissions"]), 1)
        self.assertEqual(len(fake_supabase.store["tracks"]), 2)
        self.assertEqual(len(fake_supabase.store["submissions_revisions"]), 1)

        submission = fake_supabase.store["submissions"][0]
        self.assertEqual(response["submission_id"], submission["id"])
        self.assertEqual(response["tracks_created"], 2)
        self.assertEqual(submission["idempotency_key"], "idem-create-1")
        self.assertEqual(submission["version"], 1)
        self.assertEqual(len(submission["payload"]["tracks"]), 2)

        for track in fake_supabase.store["tracks"]:
            self.assertIsNone(track["deleted_at"])
            self.assertTrue(track["client_track_id"])
            UUID(track["client_track_id"])

        for track in submission["payload"]["tracks"]:
            self.assertTrue(track["client_track_id"])
            self.assertEqual(track["local_id"], track["local_id"])

        revision = fake_supabase.store["submissions_revisions"][0]
        self.assertEqual(revision["submission_id"], submission["id"])
        self.assertEqual(revision["version"], 1)
        self.assertEqual(
            revision["payload"]["tracks"][0]["client_track_id"],
            submission["payload"]["tracks"][0]["client_track_id"],
        )

    def test_update_release_submission_reconciles_tracks_without_new_submission(self) -> None:
        existing_payload = _release_payload(
            edit_token="edit-123",
            tracks=[
                {
                    "local_id": "local-a",
                    "client_track_id": CLIENT_TRACK_ID_1,
                    "order_number": 1,
                    "title": "Faixa A",
                    "primary_artists": "Ana",
                    "authors": "Ana",
                    "explicit_content": "no",
                    "lyrics": "Letra A",
                },
                {
                    "local_id": "local-b",
                    "client_track_id": CLIENT_TRACK_ID_2,
                    "order_number": 2,
                    "title": "Faixa B",
                    "primary_artists": "Ana",
                    "authors": "Ana",
                    "explicit_content": "no",
                    "lyrics": "Letra B",
                },
            ],
        )
        fake_supabase = _FakeSupabase(
            {
                "submissions": [_submission_row(payload=existing_payload, version=3)],
                "tracks": [
                    _track_row(
                        track_id="track-1",
                        client_track_id=CLIENT_TRACK_ID_1,
                        order_number=1,
                        title="Faixa A",
                    ),
                    _track_row(
                        track_id="track-2",
                        client_track_id=CLIENT_TRACK_ID_2,
                        order_number=2,
                        title="Faixa B",
                    ),
                    _track_row(
                        track_id="track-3",
                        client_track_id=CLIENT_TRACK_ID_3,
                        order_number=3,
                        title="Faixa C",
                        deleted_at="2026-04-18T11:55:00+00:00",
                    ),
                ],
                "submissions_revisions": [],
            }
        )
        updated_payload = validate_submission_payload(
            _release_payload(
                edit_token="edit-123",
                tracks=[
                    {
                        "local_id": "local-b",
                        "order_number": 1,
                        "title": "Faixa B",
                        "primary_artists": "Ana",
                        "authors": "Ana",
                        "explicit_content": "no",
                        "lyrics": "Faixa B nova ordem",
                    },
                    {
                        "local_id": "local-c",
                        "client_track_id": CLIENT_TRACK_ID_3,
                        "order_number": 2,
                        "title": "Faixa C",
                        "primary_artists": "Ana",
                        "authors": "Ana",
                        "explicit_content": "no",
                        "lyrics": "Faixa C reativada",
                    },
                    {
                        "local_id": "local-d",
                        "order_number": 3,
                        "title": "Faixa D",
                        "primary_artists": "Ana",
                        "authors": "Ana",
                        "explicit_content": "no",
                        "lyrics": "Faixa D nova",
                    },
                ],
            )
        )

        drive_sync_calls: list[dict] = []

        def fake_queue_drive_sync(*, background_tasks, payload, submission_id):  # noqa: ARG001
            drive_sync_calls.append(
                {
                    "submission_id": submission_id,
                    "workspace_slug": getattr(payload, "workspace_slug", None),
                }
            )
            return {"ok": True, "status": "partial"}

        with (
            patch.object(submissions_module, "supabase", fake_supabase),
            patch.object(
                submissions_module,
                "_sync_airtable",
                return_value={
                    "airtable_project": {"id": "airtable-project-1"},
                    "airtable_tracks": [
                        {"id": "rec-2", "client_track_id": CLIENT_TRACK_ID_2},
                        {"id": "rec-3", "client_track_id": CLIENT_TRACK_ID_3},
                        {"id": "rec-4", "client_track_id": "new-track"},
                    ],
                    "focus_track_record_id": "rec-2",
                },
            ),
            patch.object(submissions_module, "_update_submission_airtable_success"),
            patch.object(
                submissions_module,
                "_queue_google_drive_sync",
                side_effect=fake_queue_drive_sync,
            ),
        ):
            response = submissions_module._update_release_submission(
                existing_row=deepcopy(fake_supabase.store["submissions"][0]),
                payload=updated_payload,
                now_iso="2026-04-18T12:10:00+00:00",
                idempotency_key="idem-update-1",
                background_tasks=BackgroundTasks(),
            )

        self.assertTrue(response["ok"])
        self.assertEqual(response["submission_id"], SUBMISSION_ID)
        self.assertEqual(response["tracks_created"], 3)
        self.assertEqual(response["sync"]["airtable"], "ok")
        self.assertEqual(response["airtable_project_id"], "airtable-project-1")
        self.assertEqual(len(fake_supabase.store["submissions"]), 1)
        self.assertEqual(len(fake_supabase.store["tracks"]), 4)
        self.assertEqual(len(fake_supabase.store["submissions_revisions"]), 1)

        submission = fake_supabase.store["submissions"][0]
        self.assertEqual(submission["version"], 4)
        self.assertTrue(submission["is_update"])
        self.assertEqual(submission["idempotency_key"], "idem-update-1")
        self.assertEqual(
            [track["title"] for track in submission["payload"]["tracks"]],
            ["Faixa B", "Faixa C", "Faixa D"],
        )

        tracks_by_title = {track["title"]: track for track in fake_supabase.store["tracks"]}
        self.assertEqual(tracks_by_title["Faixa B"]["client_track_id"], CLIENT_TRACK_ID_2)
        self.assertEqual(tracks_by_title["Faixa B"]["order_number"], 1)
        self.assertIsNone(tracks_by_title["Faixa B"]["deleted_at"])
        self.assertIsNone(tracks_by_title["Faixa C"]["deleted_at"])
        self.assertEqual(tracks_by_title["Faixa C"]["order_number"], 2)
        self.assertIsNotNone(tracks_by_title["Faixa A"]["deleted_at"])
        self.assertEqual(tracks_by_title["Faixa D"]["order_number"], 3)
        self.assertTrue(tracks_by_title["Faixa D"]["client_track_id"])

        # Drive sync must be queued on edit pós-submit (PR #12). Exactly one
        # call, targeting the same submission id and carrying the payload
        # workspace slug so folder reuse can land on the persisted folder.
        self.assertEqual(len(drive_sync_calls), 1)
        self.assertEqual(drive_sync_calls[0]["submission_id"], SUBMISSION_ID)
        self.assertEqual(drive_sync_calls[0]["workspace_slug"], "atabaque")
        self.assertEqual(response["drive_sync"], {"ok": True, "status": "partial"})

    def test_update_release_submission_queues_google_drive_sync_on_edit(self) -> None:
        """PR #12: the edit pós-submit path must queue Google Drive sync
        exactly once so folder reuse / rename (PR #10) is exercised on
        updates, matching the behavior of the initial submit path."""

        existing_payload = _release_payload(
            edit_token="edit-drive",
            tracks=[
                {
                    "local_id": "local-a",
                    "client_track_id": CLIENT_TRACK_ID_1,
                    "order_number": 1,
                    "title": "Faixa A",
                    "primary_artists": "Ana",
                    "authors": "Ana",
                    "explicit_content": "no",
                    "lyrics": "Letra A",
                },
            ],
        )
        fake_supabase = _FakeSupabase(
            {
                "submissions": [_submission_row(payload=existing_payload, version=1)],
                "tracks": [
                    _track_row(
                        track_id="track-1",
                        client_track_id=CLIENT_TRACK_ID_1,
                        order_number=1,
                        title="Faixa A",
                    ),
                ],
                "submissions_revisions": [],
            }
        )

        updated_payload = validate_submission_payload(
            _release_payload(
                edit_token="edit-drive",
                tracks=[
                    {
                        "local_id": "local-a",
                        "client_track_id": CLIENT_TRACK_ID_1,
                        "order_number": 1,
                        "title": "Faixa A (editada)",
                        "primary_artists": "Ana",
                        "authors": "Ana",
                        "explicit_content": "no",
                        "lyrics": "Letra A editada",
                    },
                ],
            )
        )

        queue_calls: list[dict] = []

        def fake_queue_drive_sync(*, background_tasks, payload, submission_id):  # noqa: ARG001
            queue_calls.append(
                {
                    "submission_id": submission_id,
                    "workspace_slug": payload.workspace_slug,
                }
            )
            return {"ok": True, "status": "partial"}

        with (
            patch.object(submissions_module, "supabase", fake_supabase),
            patch.object(
                submissions_module,
                "_sync_airtable",
                return_value={
                    "airtable_project": {"id": "airtable-project-1"},
                    "airtable_tracks": [],
                    "focus_track_record_id": None,
                },
            ),
            patch.object(submissions_module, "_update_submission_airtable_success"),
            patch.object(
                submissions_module,
                "_queue_google_drive_sync",
                side_effect=fake_queue_drive_sync,
            ),
        ):
            response = submissions_module._update_release_submission(
                existing_row=deepcopy(fake_supabase.store["submissions"][0]),
                payload=updated_payload,
                now_iso="2026-04-18T12:30:00+00:00",
                idempotency_key="idem-update-drive",
                background_tasks=BackgroundTasks(),
            )

        self.assertTrue(response["ok"])
        self.assertEqual(len(queue_calls), 1)
        self.assertEqual(queue_calls[0]["submission_id"], SUBMISSION_ID)
        self.assertEqual(queue_calls[0]["workspace_slug"], "atabaque")
        self.assertIn("drive_sync", response)
        self.assertEqual(response["drive_sync"], {"ok": True, "status": "partial"})

    def test_load_edit_submission_backfills_legacy_client_track_ids(self) -> None:
        legacy_payload = _release_payload(
            edit_token="edit-123",
            tracks=[
                {
                    "local_id": "legacy-a",
                    "order_number": 1,
                    "title": "Faixa A",
                    "primary_artists": "Ana",
                    "authors": "Ana",
                    "explicit_content": "no",
                    "lyrics": "Letra A",
                },
                {
                    "local_id": "legacy-b",
                    "order_number": 2,
                    "title": "Faixa B",
                    "primary_artists": "Ana",
                    "authors": "Ana",
                    "explicit_content": "no",
                    "lyrics": "Letra B",
                },
            ],
        )
        fake_supabase = _FakeSupabase(
            {
                "submissions": [_submission_row(payload=legacy_payload)],
                "tracks": [
                    _track_row(
                        track_id="track-1",
                        client_track_id=None,
                        order_number=1,
                        title="Faixa A",
                    ),
                    _track_row(
                        track_id="track-2",
                        client_track_id=None,
                        order_number=2,
                        title="Faixa B",
                    ),
                ],
            }
        )

        with patch.object(submissions_module, "supabase", fake_supabase):
            response = asyncio.run(submissions_module.load_edit_submission("edit-123"))

        self.assertTrue(response["ok"])
        returned_tracks = response["data"]["tracks"]
        self.assertEqual(len(returned_tracks), 2)
        self.assertTrue(returned_tracks[0]["client_track_id"])
        self.assertTrue(returned_tracks[1]["client_track_id"])
        self.assertEqual(
            fake_supabase.store["tracks"][0]["client_track_id"],
            returned_tracks[0]["client_track_id"],
        )
        self.assertEqual(
            fake_supabase.store["submissions"][0]["payload"]["tracks"][0]["client_track_id"],
            returned_tracks[0]["client_track_id"],
        )

    def test_create_submission_replays_recent_idempotent_request(self) -> None:
        existing_payload = _release_payload()
        fake_supabase = _FakeSupabase(
            {
                "submissions": [_submission_row(payload=existing_payload)],
                "tracks": [
                    _track_row(
                        track_id="track-1",
                        client_track_id=CLIENT_TRACK_ID_1,
                        order_number=1,
                        title="Faixa A",
                    ),
                    _track_row(
                        track_id="track-2",
                        client_track_id=CLIENT_TRACK_ID_2,
                        order_number=2,
                        title="Faixa B",
                    ),
                ],
            }
        )

        with (
            patch.object(submissions_module, "supabase", fake_supabase),
            patch.object(
                submissions_module,
                "_load_recent_idempotent_submission",
                return_value=deepcopy(fake_supabase.store["submissions"][0]),
            ),
        ):
            response = submissions_module.create_submission(
                _release_payload(),
                BackgroundTasks(),
                idempotency_key="idem-create-1",
            )

        self.assertTrue(response["ok"])
        self.assertTrue(response["replayed"])
        self.assertEqual(response["submission_id"], SUBMISSION_ID)
        self.assertEqual(len(fake_supabase.store["submissions"]), 1)
        self.assertEqual(len(fake_supabase.store["tracks"]), 2)

    def test_create_submission_edit_path_does_not_call_summary_email_helper(self) -> None:
        with (
            patch.object(
                submissions_module,
                "_load_submission_by_edit_token",
                return_value=_submission_row(payload=_release_payload(edit_token="edit-123")),
            ),
            patch.object(
                submissions_module,
                "_load_recent_idempotent_submission",
                return_value=None,
            ),
            patch.object(
                submissions_module,
                "_update_release_submission",
                return_value={"ok": True, "submission_id": SUBMISSION_ID},
            ) as update_mock,
            patch.object(
                submissions_module,
                "_maybe_send_submission_summary_email",
            ) as summary_email_mock,
        ):
            response = submissions_module.create_submission(
                _release_payload(edit_token="edit-123"),
                BackgroundTasks(),
                idempotency_key="idem-edit-1",
            )

        self.assertEqual(response["submission_id"], SUBMISSION_ID)
        update_mock.assert_called_once()
        summary_email_mock.assert_not_called()

    def test_create_submission_replays_recent_rights_clearance_request(self) -> None:
        existing_payload = _rights_clearance_payload()
        existing_row = {
            "id": "rights-sub-1",
            "draft_token": DRAFT_TOKEN,
            "edit_token": "rights-edit-1",
            "client_slug": "atabaque",
            "payload": existing_payload,
            "email_status": "pending",
            "airtable_sync_status": "skipped",
            "summary_email_sent": False,
            "created_at": "2026-04-18T12:00:00+00:00",
            "updated_at": "2026-04-18T12:00:00+00:00",
        }
        fake_supabase = _FakeSupabase(
            {
                "submissions": [existing_row],
                "tracks": [],
            }
        )

        with (
            patch.object(submissions_module, "supabase", fake_supabase),
            patch.object(
                submissions_module,
                "_load_recent_idempotent_submission",
                return_value=deepcopy(existing_row),
            ),
        ):
            response = submissions_module.create_submission(
                _rights_clearance_payload(),
                BackgroundTasks(),
                idempotency_key="idem-rights-1",
            )

        self.assertTrue(response["replayed"])
        self.assertEqual(response["submission_id"], "rights-sub-1")
        self.assertEqual(response["workflow"]["workflow_type"], "rights_clearance")


if __name__ == "__main__":
    unittest.main()
