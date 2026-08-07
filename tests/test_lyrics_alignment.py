from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.modules import lyrics_alignment
from app.services.lyrics_alignment import _normalize_result, _upload_file


app = FastAPI()
app.include_router(lyrics_alignment.router)
client = TestClient(app)


class LyricsAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.original_key = settings.GEMINI_LYRICS_API_KEY
        settings.GEMINI_LYRICS_API_KEY = "test-key"
        lyrics_alignment._requests_by_client.clear()

    def tearDown(self):
        settings.GEMINI_LYRICS_API_KEY = self.original_key
        lyrics_alignment._requests_by_client.clear()

    def test_requires_explicit_ai_processing_consent(self):
        response = client.post(
            "/lyrics/align",
            data={"workspace_slug": "atabaque", "lyrics": "Linha um"},
            files={"audio": ("song.wav", b"RIFFtest", "audio/wav")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Confirme", response.json()["detail"])

    def test_rejects_unsupported_audio(self):
        response = client.post(
            "/lyrics/align",
            data={"workspace_slug": "atabaque", "lyrics": "Linha um", "consent_ai_processing": "true"},
            files={"audio": ("song.exe", b"not-audio", "application/octet-stream")},
        )
        self.assertEqual(response.status_code, 415)

    @patch("app.modules.lyrics_alignment.align_lyrics_with_gemini")
    def test_returns_provider_alignment(self, align_mock):
        align_mock.return_value = {
            "ok": True,
            "provider": "gemini",
            "model": "test-model",
            "duration_ms": 2000,
            "lines": [
                {
                    "id": "line-001",
                    "text": "Linha um",
                    "start_ms": 100,
                    "end_ms": 900,
                    "confidence": 0.9,
                    "status": "timed",
                    "needs_review": False,
                }
            ],
        }
        response = client.post(
            "/lyrics/align",
            data={"workspace_slug": "atabaque", "lyrics": "Linha um", "consent_ai_processing": "true"},
            files={"audio": ("song.wav", b"RIFFtest", "audio/wav")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lines"][0]["text"], "Linha um")
        self.assertEqual(align_mock.call_args.kwargs["size_bytes"], 8)

    def test_normalization_preserves_approved_text_and_flags_invalid_time(self):
        result = _normalize_result(
            {
                "lines": [
                    {"line_id": "line-001", "status": "timed", "start_ms": 500, "end_ms": 1000, "confidence": 0.9},
                    {"line_id": "line-002", "status": "timed", "start_ms": 400, "end_ms": 1200, "confidence": 0.9},
                ]
            },
            [
                {"id": "line-001", "text": "Texto aprovado"},
                {"id": "line-002", "text": "Refrão aprovado"},
            ],
        )
        self.assertEqual(result["lines"][0]["text"], "Texto aprovado")
        self.assertEqual(result["lines"][1]["text"], "Refrão aprovado")
        self.assertEqual(result["lines"][1]["status"], "unmatched")
        self.assertTrue(result["lines"][1]["needs_review"])

    def test_api_key_is_sent_in_header_not_query_string(self):
        fake_client = MagicMock()
        fake_client.post.side_effect = [
            httpx.Response(200, headers={"x-goog-upload-url": "https://upload.example/session"}, json={}),
            httpx.Response(200, json={"file": {"name": "files/abc", "uri": "https://files.example/abc"}}),
        ]

        _upload_file(
            fake_client,
            audio_file=io.BytesIO(b"RIFFtest"),
            filename="song.wav",
            mime_type="audio/wav",
            size_bytes=8,
        )

        init_call = fake_client.post.call_args_list[0]
        self.assertNotIn("params", init_call.kwargs)
        self.assertEqual(init_call.kwargs["headers"]["x-goog-api-key"], "test-key")


if __name__ == "__main__":
    unittest.main()
