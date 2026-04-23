from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")

from app.services import google_drive as google_drive_module


class _Executable:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def execute(self) -> dict:
        return dict(self.payload)


class _FakeFilesApi:
    def __init__(self) -> None:
        self.update_calls: list[dict] = []

    def update(self, **kwargs: object) -> _Executable:
        self.update_calls.append(dict(kwargs))
        body = kwargs.get("body") or {}
        payload = {
            "id": kwargs["fileId"],
            "name": body.get("name"),
        }
        return _Executable(payload)


class _FakeDriveService:
    def __init__(self) -> None:
        self.files_api = _FakeFilesApi()

    def files(self) -> _FakeFilesApi:
        return self.files_api


def _payload(project_title: str) -> SimpleNamespace:
    return SimpleNamespace(
        identification=SimpleNamespace(
            release_type="single",
            project_title=project_title,
        )
    )


class GoogleDriveFolderReuseTests(unittest.TestCase):
    def test_ensure_project_folder_reuses_persisted_folder_id(self) -> None:
        service = _FakeDriveService()
        submission = {
            "id": "sub-123",
            "google_drive_folder_id": "folder-123",
            "parent_folder_id": "parent-1",
            "payload": _payload("Projeto Teste"),
        }

        with (
            patch.object(
                google_drive_module,
                "_get_folder_by_id",
                return_value={"id": "folder-123", "name": "Single_Projeto Teste"},
            ),
            patch.object(google_drive_module, "_ensure_folder") as ensure_folder_mock,
            patch.object(
                google_drive_module,
                "_persist_submission_google_drive_folder_id",
            ) as persist_mock,
        ):
            result = google_drive_module.ensure_project_folder(service, submission)

        self.assertEqual(result["id"], "folder-123")
        self.assertFalse(result["created"])
        self.assertFalse(result["renamed"])
        ensure_folder_mock.assert_not_called()
        persist_mock.assert_called_once_with("sub-123", "folder-123")

    def test_ensure_project_folder_renames_when_release_title_changes(self) -> None:
        service = _FakeDriveService()
        submission = {
            "id": "sub-123",
            "google_drive_folder_id": "folder-123",
            "parent_folder_id": "parent-1",
            "payload": _payload("Projeto Novo"),
        }

        with (
            patch.object(
                google_drive_module,
                "_get_folder_by_id",
                return_value={"id": "folder-123", "name": "Single_Projeto Antigo"},
            ),
            patch.object(
                google_drive_module,
                "_persist_submission_google_drive_folder_id",
            ) as persist_mock,
        ):
            result = google_drive_module.ensure_project_folder(service, submission)

        self.assertTrue(result["renamed"])
        self.assertEqual(result["name"], "Single_Projeto Novo")
        self.assertEqual(len(service.files_api.update_calls), 1)
        self.assertEqual(
            service.files_api.update_calls[0]["body"]["name"],
            "Single_Projeto Novo",
        )
        persist_mock.assert_called_once_with("sub-123", "folder-123")

    def test_ensure_project_folder_uses_fallback_and_persists_legacy_id(self) -> None:
        service = _FakeDriveService()
        submission = {
            "id": "sub-123",
            "google_drive_folder_id": None,
            "parent_folder_id": "parent-1",
            "payload": _payload("Projeto Teste"),
        }

        with (
            patch.object(google_drive_module, "_get_folder_by_id", return_value=None),
            patch.object(
                google_drive_module,
                "_ensure_folder",
                return_value={
                    "id": "folder-legacy",
                    "name": "Single_Projeto Teste",
                    "created": False,
                },
            ) as ensure_folder_mock,
            patch.object(
                google_drive_module,
                "_persist_submission_google_drive_folder_id",
            ) as persist_mock,
        ):
            result = google_drive_module.ensure_project_folder(service, submission)

        self.assertEqual(result["id"], "folder-legacy")
        self.assertFalse(result["renamed"])
        ensure_folder_mock.assert_called_once()
        persist_mock.assert_called_once_with("sub-123", "folder-legacy")


if __name__ == "__main__":
    unittest.main()
