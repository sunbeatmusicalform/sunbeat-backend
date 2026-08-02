from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.schemas.submission import validate_submission_payload


class SubmissionSchemaTests(unittest.TestCase):
    def test_release_intake_preserves_edit_token_and_client_track_id(self) -> None:
        payload = validate_submission_payload(
            {
                "draft_token": "2d46d148-c992-4b60-87c9-49c7b9c6d2ef",
                "edit_token": "edit-token-123",
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
                },
                "tracks": [
                    {
                        "local_id": "track-1",
                        "client_track_id": "b284c498-58be-4a1f-b796-a7548f54e83d",
                        "order_number": 1,
                        "title": "Faixa 1",
                        "primary_artists": "Ana",
                        "primary_artist_refs": [
                            {
                                "id": "person-ana",
                                "name": "Ana",
                                "status": "registered",
                                "source": "people_registry",
                            }
                        ],
                        "authors": "Ana",
                    }
                ],
            }
        )

        self.assertEqual(payload.edit_token, "edit-token-123")
        self.assertEqual(
            payload.tracks[0].client_track_id,
            "b284c498-58be-4a1f-b796-a7548f54e83d",
        )

        dumped = payload.model_dump()
        self.assertEqual(dumped["edit_token"], "edit-token-123")
        self.assertEqual(
            dumped["tracks"][0]["client_track_id"],
            "b284c498-58be-4a1f-b796-a7548f54e83d",
        )
        self.assertEqual(
            dumped["tracks"][0]["primary_artist_refs"][0]["id"],
            "person-ana",
        )
        self.assertTrue(dumped["tracks"][0]["is_focus_track"])

    def test_single_rejects_more_than_one_track(self) -> None:
        track = {
            "local_id": "track-1",
            "order_number": 1,
            "title": "Faixa 1",
            "primary_artists": "Ana",
            "authors": "Ana",
        }
        with self.assertRaisesRegex(ValidationError, "exatamente uma faixa"):
            validate_submission_payload({
                "draft_token": "draft-1",
                "workspace_slug": "atabaque",
                "workflow_type": "release_intake",
                "identification": {
                    "submitter_name": "Ana",
                    "submitter_email": "ana@example.com",
                    "project_title": "Single inválido",
                    "release_type": "single",
                },
                "project": {"release_date": "2026-05-01"},
                "tracks": [track, {**track, "local_id": "track-2", "order_number": 2}],
            })


if __name__ == "__main__":
    unittest.main()
