from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.modules import release_drafts
from app.services import email as email_module


class _FakeDraftTable:
    def __init__(
        self,
        select_rows: list[dict | None],
        updates: list[dict],
        update_error: Exception | None = None,
        update_results: list[list[dict] | None] | None = None,
    ) -> None:
        self.select_rows = select_rows
        self.updates = updates
        self.update_error = update_error
        self.update_results = update_results or []
        self.mode: str | None = None
        self.payload: dict | None = None
        self.eq_calls: list[tuple[str, object]] = []

    def select(self, *_args: object) -> "_FakeDraftTable":
        self.mode = "select"
        self.eq_calls = []
        return self

    def update(self, payload: dict) -> "_FakeDraftTable":
        self.mode = "update"
        self.payload = payload
        self.eq_calls = []
        return self

    def eq(self, key: str, value: object) -> "_FakeDraftTable":
        self.eq_calls.append((key, value))
        return self

    def limit(self, _value: int) -> "_FakeDraftTable":
        return self

    def execute(self) -> SimpleNamespace:
        if self.mode == "select":
            row = self.select_rows.pop(0) if self.select_rows else None
            return SimpleNamespace(data=[row] if row is not None else [])

        if self.mode == "update":
            if self.update_error is not None:
                raise self.update_error

            self.updates.append(
                {
                    "payload": self.payload,
                    "eq_calls": list(self.eq_calls),
                }
            )

            result_data = (
                self.update_results.pop(0)
                if self.update_results
                else [self.payload]
            )
            return SimpleNamespace(data=result_data)

        raise AssertionError(f"Unexpected table mode: {self.mode}")


class _FakeSupabase:
    def __init__(
        self,
        select_rows: list[dict | None],
        updates: list[dict],
        update_error: Exception | None = None,
        update_results: list[list[dict] | None] | None = None,
    ) -> None:
        self.select_rows = select_rows
        self.updates = updates
        self.update_error = update_error
        self.update_results = update_results or []

    def table(self, name: str) -> _FakeDraftTable:
        if name != "release_intake_drafts":
            raise AssertionError(f"Unexpected table requested: {name}")
        return _FakeDraftTable(
            self.select_rows,
            self.updates,
            self.update_error,
            self.update_results,
        )


def _meta(
    *,
    first_stage_completion_email_sent: bool = False,
    first_stage_completion_email_sent_at: str | None = None,
    first_stage_completion_email_message_id: str | None = None,
) -> dict:
    payload = {
        "workflow_type": "release_intake",
        "form_version": "legacy_v1",
        "source": "sunbeat.atabaque.release_intake.legacy_v1",
    }
    if first_stage_completion_email_sent:
        payload["first_stage_completion_email_sent"] = True
        payload["first_stage_completion_email_sent_at"] = (
            first_stage_completion_email_sent_at
            or "2026-04-17T19:00:00+00:00"
        )
        payload["first_stage_completion_email_message_id"] = (
            first_stage_completion_email_message_id
        )
    return payload


def _draft(
    *,
    current_step: str = "release",
    meta: dict | None = None,
) -> dict:
    return {
        "draft_token": "draft-123",
        "client_slug": "atabaque",
        "current_step": current_step,
        "updated_at": "2026-04-17T19:59:00+00:00",
        "values": {
            "identification": {
                "submitter_email": "ana@example.com",
                "submitter_name": "Ana",
                "project_title": "Projeto Teste",
            }
        },
        "meta": meta or _meta(),
    }


class ReleaseDraftFirstStageEmailTests(unittest.TestCase):
    def test_sent_and_flag_persisted(self) -> None:
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
                return_value={
                    "status": "sent",
                    "provider_message_id": "msg-123",
                },
            ) as email_mock,
            patch.object(
                release_drafts,
                "utc_now_iso",
                return_value="2026-04-17T20:00:00+00:00",
            ),
            patch.object(
                release_drafts,
                "supabase",
                _FakeSupabase(
                    [
                        {
                            "draft_token": "draft-123",
                            "updated_at": "2026-04-17T19:59:00+00:00",
                            "meta": _meta(),
                        }
                    ],
                    updates,
                ),
            ),
        ):
            meta = release_drafts._maybe_send_first_stage_completion_email(draft)

        self.assertTrue(meta["first_stage_completion_email_sent"])
        self.assertEqual(
            meta["first_stage_completion_email_sent_at"],
            "2026-04-17T20:00:00+00:00",
        )
        self.assertEqual(meta["first_stage_completion_email_message_id"], "msg-123")
        self.assertEqual(meta["first_stage_completion_email_step"], "release")
        self.assertEqual(
            email_mock.call_args.kwargs["idempotency_key"],
            "draft-123:first_stage",
        )
        self.assertEqual(len(updates), 1)
        self.assertEqual(
            updates[0]["eq_calls"],
            [
                ("draft_token", "draft-123"),
                ("updated_at", "2026-04-17T19:59:00+00:00"),
            ],
        )

    def test_200_without_message_id_treated_as_sent(self) -> None:
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
                return_value={
                    "status": "sent_without_message_id",
                    "provider_message_id": None,
                    "provider_response": {"accepted": True},
                },
            ),
            patch.object(
                release_drafts,
                "utc_now_iso",
                return_value="2026-04-17T20:00:00+00:00",
            ),
            patch.object(
                release_drafts,
                "supabase",
                _FakeSupabase(
                    [
                        {
                            "draft_token": "draft-123",
                            "updated_at": "2026-04-17T19:59:00+00:00",
                            "meta": _meta(),
                        }
                    ],
                    updates,
                ),
            ),
        ):
            meta = release_drafts._maybe_send_first_stage_completion_email(draft)

        self.assertTrue(meta["first_stage_completion_email_sent"])
        self.assertIsNone(meta["first_stage_completion_email_message_id"])
        self.assertTrue(updates[0]["payload"]["meta"]["first_stage_completion_email_sent"])
        self.assertIsNone(
            updates[0]["payload"]["meta"]["first_stage_completion_email_message_id"]
        )

    def test_short_circuit_when_flag_already_true(self) -> None:
        updates: list[dict] = []
        draft = _draft(
            meta=_meta(
                first_stage_completion_email_sent=True,
                first_stage_completion_email_sent_at="2026-04-17T19:00:00+00:00",
                first_stage_completion_email_message_id="msg-existing",
            )
        )

        with (
            patch.object(
                release_drafts,
                "_load_workspace_email_settings",
            ) as settings_mock,
            patch.object(
                release_drafts,
                "send_first_stage_completion_email",
            ) as email_mock,
            patch.object(release_drafts, "supabase", _FakeSupabase([], updates)),
        ):
            meta = release_drafts._maybe_send_first_stage_completion_email(draft)

        self.assertTrue(meta["first_stage_completion_email_sent"])
        self.assertEqual(
            meta["first_stage_completion_email_sent_at"],
            "2026-04-17T19:00:00+00:00",
        )
        self.assertEqual(meta["first_stage_completion_email_message_id"], "msg-existing")
        settings_mock.assert_not_called()
        email_mock.assert_not_called()
        self.assertEqual(updates, [])

    def test_already_sent_by_other_on_parallel_write(self) -> None:
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
                return_value={
                    "status": "sent",
                    "provider_message_id": "msg-123",
                },
            ),
            patch.object(
                release_drafts,
                "supabase",
                _FakeSupabase(
                    [
                        {
                            "draft_token": "draft-123",
                            "updated_at": "2026-04-17T19:59:30+00:00",
                            "meta": _meta(
                                first_stage_completion_email_sent=True,
                                first_stage_completion_email_sent_at="2026-04-17T20:00:00+00:00",
                                first_stage_completion_email_message_id="msg-race",
                            ),
                        }
                    ],
                    updates,
                ),
            ),
        ):
            meta = release_drafts._maybe_send_first_stage_completion_email(draft)

        self.assertTrue(meta["first_stage_completion_email_sent"])
        self.assertEqual(meta["first_stage_completion_email_message_id"], "msg-race")
        self.assertEqual(updates, [])

    def test_sent_but_flag_failed(self) -> None:
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
                return_value={
                    "status": "sent",
                    "provider_message_id": "msg-123",
                },
            ),
            patch.object(
                release_drafts,
                "supabase",
                _FakeSupabase(
                    [
                        {
                            "draft_token": "draft-123",
                            "updated_at": "2026-04-17T19:59:00+00:00",
                            "meta": _meta(),
                        }
                    ],
                    updates,
                    update_error=RuntimeError("db down"),
                ),
            ),
        ):
            meta = release_drafts._maybe_send_first_stage_completion_email(draft)

        self.assertNotIn("first_stage_completion_email_sent", meta)
        self.assertEqual(updates, [])


class SendFirstStageCompletionEmailTests(unittest.TestCase):
    def test_idempotency_key_forwarded_to_resend(self) -> None:
        with patch.object(
            email_module,
            "_post_resend",
            return_value={"provider_message_id": "msg-123"},
        ) as post_mock:
            result = email_module.send_first_stage_completion_email(
                to_emails=["labels@atabaque.biz"],
                workspace_name="Atabaque",
                submitter_name="Ana",
                submitter_email="ana@example.com",
                project_title="Projeto Teste",
                draft_token="draft-123",
                current_step="release",
                idempotency_key="draft-123:first_stage",
            )

        self.assertEqual(result["status"], "sent")
        self.assertEqual(
            post_mock.call_args.kwargs["idempotency_key"],
            "draft-123:first_stage",
        )

    def test_200_without_message_id_treated_as_sent(self) -> None:
        with patch.object(
            email_module,
            "_post_resend",
            return_value={
                "provider_status_code": 200,
                "provider_response": {"accepted": True},
                "provider_message_id": None,
            },
        ):
            result = email_module.send_first_stage_completion_email(
                to_emails=["labels@atabaque.biz"],
                workspace_name="Atabaque",
                submitter_name="Ana",
                submitter_email="ana@example.com",
                project_title="Projeto Teste",
                draft_token="draft-123",
                current_step="release",
            )

        self.assertEqual(result["status"], "sent_without_message_id")
        self.assertIsNone(result["provider_message_id"])


if __name__ == "__main__":
    unittest.main()
