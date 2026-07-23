from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")

from app.services import google_drive as google_drive_module


def _payload(*, workspace_slug: str = "atabaque") -> SimpleNamespace:
    return SimpleNamespace(
        workspace_slug=workspace_slug,
        identification=SimpleNamespace(
            release_type="single",
            project_title="Projeto Teste",
            submitter_email="qa@example.com",
        ),
        tracks=[SimpleNamespace(primary_artists="Donna Lolla")],
    )


class GoogleDriveClientRoutingTests(unittest.TestCase):
    def test_invalid_artist_cache_falls_back_to_drive_link(self) -> None:
        service = object()
        matching = {
            "record": {
                "id": "rec-client",
                "fields": {
                    "folder_id_artista": "invalid-cached-id",
                    "folder_id_projetos": "projects-123",
                    "Pasta do Drive": "https://drive.google.com/drive/folders/artist-correct",
                },
            },
            "client_name": "Donna Lolla",
            "label_name": "Label",
        }

        with (
            patch.object(
                google_drive_module,
                "_resolve_matching_client_record",
                return_value=matching,
            ),
            patch.object(
                google_drive_module,
                "_get_folder_by_id",
                side_effect=[
                    None,
                    {"id": "artist-correct", "name": "Donna Lolla"},
                ],
            ) as get_folder_mock,
            patch.object(
                google_drive_module,
                "_ensure_folder",
                return_value={
                    "id": "projects-123",
                    "name": "Projetos",
                    "created": False,
                },
            ),
            patch.object(google_drive_module, "_airtable_update_record") as update_mock,
        ):
            folder_id, routing = google_drive_module._resolve_parent_folder(
                service,
                _payload(),
            )

        self.assertEqual(folder_id, "projects-123")
        self.assertEqual(routing["artist_folder_source"], "pasta_do_drive_fallback")
        self.assertEqual(
            get_folder_mock.call_args_list,
            [call(service, "invalid-cached-id"), call(service, "artist-correct")],
        )
        update_mock.assert_not_called()

    def test_root_fallback_is_blocked_for_atabaque(self) -> None:
        with (
            patch.object(
                google_drive_module,
                "_resolve_matching_client_record",
                return_value=None,
            ),
            patch.object(
                google_drive_module.settings,
                "GOOGLE_DRIVE_ROOT_FOLDER_ID",
                "root-folder",
            ),
            patch.object(
                google_drive_module.settings,
                "GOOGLE_DRIVE_ATABAQUE_ALLOW_ROOT_FALLBACK",
                False,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "root fallback is disabled"):
                google_drive_module._resolve_parent_folder(object(), _payload())

    def test_root_fallback_remains_available_for_other_workspaces(self) -> None:
        with (
            patch.object(
                google_drive_module,
                "_resolve_matching_client_record",
                return_value=None,
            ),
            patch.object(
                google_drive_module.settings,
                "GOOGLE_DRIVE_ROOT_FOLDER_ID",
                "root-folder",
            ),
        ):
            folder_id, routing = google_drive_module._resolve_parent_folder(
                object(),
                _payload(workspace_slug="outro-workspace"),
            )

        self.assertEqual(folder_id, "root-folder")
        self.assertEqual(routing["strategy"], "root_fallback")

    def test_client_audit_is_read_only_and_reports_missing_cache(self) -> None:
        records = [
            {
                "id": "rec-client",
                "fields": {
                    "Clientes": "Cliente Teste",
                    "folder_id_artista": "artist-123",
                    "Pasta do Drive": "https://drive.google.com/drive/folders/artist-123",
                },
            }
        ]

        with (
            patch.object(
                google_drive_module,
                "_airtable_list_records",
                return_value=records,
            ),
            patch.object(
                google_drive_module,
                "_resolve_accessible_artist_folder",
                return_value=(
                    {"id": "artist-123", "name": "Cliente Teste"},
                    "folder_id_artista",
                ),
            ),
            patch.object(
                google_drive_module,
                "_find_child_folder",
                return_value={
                    "id": "projects-123",
                    "name": "Projetos",
                    "created": False,
                },
            ),
            patch.object(google_drive_module, "_ensure_folder") as ensure_mock,
            patch.object(google_drive_module, "_airtable_update_record") as update_mock,
        ):
            result = google_drive_module.audit_airtable_client_drive_folders(
                service=object()
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "read_only")
        self.assertEqual(result["status_counts"], {"projects_cache_missing": 1})
        ensure_mock.assert_not_called()
        update_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
