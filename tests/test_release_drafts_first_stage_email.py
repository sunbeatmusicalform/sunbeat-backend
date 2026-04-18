from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.modules import release_drafts


class _FakeDraftTable:
    def __init__(self, updates: list[dict]) -> None:
        self.updates = updates
        self.payload: dict | None = None
        self.eq_key: str | None = None
        self.eq_value: str | None = None

    def update(self, payload: dict) -> "_FakeDraftTable":
        self.payload = payload
        return self

    def eq(self, key: str, value: str) -> "_FakeDraftTable":
        self.eq_key = key
        self.eq_value = value
        return self

    def execute(self) -> SimpleNamespace:
        self.updates.append(
            {
                "payload": self.payload,
                "eq_key": self.eq_key,
                "eq_value": self.eq_value,
            }
        )
        return SimpleNamespace(data=[self.payload])


class _FakeSupabase:
    def __init__(self, updates: list[dict]) -> None:
        self.updates = updates

    def table(self, name: str) -> _FakeDraftTable:
        if name != "release_intake_drafts":
            raise AssertionError(f"Unexpected table requested: {name}")
        return _FakeDraftTable(self.updates)


def _draft(
    *,
    current_step: str = "release",
    meta: dict | None = None,
) -> dict:
    return {
        "draft_token": "draft-123",
        "client_slug": "atabaque",
        "current_step": current_step,
        "values": {
            "identification": {
                "submitter_email": "ana@example.com",
                "submitter_name": "Ana",
                "project_title": "Projeto Teste",
            }
        },
        "meta": meta
        or {
            "workflow_type": "release_intake",
            "form_version": "legacy_v1",
            "source": "sunbeat.atabaque.release_intake.legacy_v1",
        },
    }


class ReleaseDraftFirstStageEmailTests(unittest.TestCase):
    def test_sends_first_stage_completion_email_once(self) -> None:
        updates: list[dict] = []
        draft = _draft()

        with (
            patch.object(
                release_drafts,
                "_load_workspace_email_settings",
                return_value={
                    "workspace_name": "Atabaque",
                    "submission_email_enabled": True,
                    "notification_emails": ["labels@atabaque.biz"],
                },
            ),
            patch.object(
                release_drafts,
                "send_first_stage_completion_email",
                return_value={"provider_message_id": "msg-123"},
            ) as email_mock,
            patch.object(release_drafts, "utc_now_iso", return_value="2026-04-17T20:00:00+00:00"),
            patch.object(release_drafts, "supabase", _FakeSupabase(updates)),
        ):
            meta = release_drafts._maybe_send_first_stage_completion_email(draft)

        self.assertTrue(meta["first_stage_completion_email_sent"])
        self.assertEqual(
            meta["first_stage_completion_email_sent_at"],
            "2026-04-17T20:00:00+00:00",
        )
        self.assertEqual(meta["first_stage_completion_email_message_id"], "msg-123")
        self.assertEqual(meta["first_stage_completion_email_step"], "release")
        email_mock.assert_called_once()
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["eq_key"], "draft_token")
        self.assertEqual(updates[0]["eq_value"], "draft-123")

    def test_skips_duplicate_send_when_meta_flag_already_exists(self) -> None:
        updates: list[dict] = []
        draft = _draft(
            meta={
                "workflow_type": "release_intake",
                "form_version": "legacy_v1",
                "source": "sunbeat.atabaque.release_intake.legacy_v1",
                "first_stage_completion_email_sent": True,
                "first_stage_completion_email_sent_at": "2026-04-17T19:00:00+00:00",
            }
        )

        with (
            patch.object(release_drafts, "send_first_stage_completion_email") as email_mock,
            patch.object(release_drafts, "supabase", _FakeSupabase(updates)),
        ):
            meta = release_drafts._maybe_send_first_stage_completion_email(draft)

        self.assertTrue(meta["first_stage_completion_email_sent"])
        self.assertEqual(
            meta["first_stage_completion_email_sent_at"],
            "2026-04-17T19:00:00+00:00",
        )
        email_mock.assert_not_called()
        self.assertEqual(updates, [])


if __name__ == "__main__":
    unittest.main()
