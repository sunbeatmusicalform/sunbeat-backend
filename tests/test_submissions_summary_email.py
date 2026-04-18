from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.modules import submissions as submissions_module
from app.services import email as email_module


class _FakeSubmissionsTable:
    def __init__(
        self,
        select_rows: list[dict | None],
        updates: list[dict],
        update_error: Exception | None = None,
    ) -> None:
        self.select_rows = select_rows
        self.updates = updates
        self.update_error = update_error
        self.mode: str | None = None
        self.payload: dict | None = None
        self.eq_key: str | None = None
        self.eq_value: str | None = None

    def select(self, *_args: object) -> "_FakeSubmissionsTable":
        self.mode = "select"
        return self

    def update(self, payload: dict) -> "_FakeSubmissionsTable":
        self.mode = "update"
        self.payload = payload
        return self

    def eq(self, key: str, value: str) -> "_FakeSubmissionsTable":
        self.eq_key = key
        self.eq_value = value
        return self

    def limit(self, _value: int) -> "_FakeSubmissionsTable":
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
                    "eq_key": self.eq_key,
                    "eq_value": self.eq_value,
                }
            )
            return SimpleNamespace(data=[self.payload])

        raise AssertionError(f"Unexpected table mode: {self.mode}")


class _FakeSupabase:
    def __init__(
        self,
        select_rows: list[dict | None],
        updates: list[dict],
        update_error: Exception | None = None,
    ) -> None:
        self.select_rows = select_rows
        self.updates = updates
        self.update_error = update_error

    def table(self, name: str) -> _FakeSubmissionsTable:
        if name != "submissions":
            raise AssertionError(f"Unexpected table requested: {name}")
        return _FakeSubmissionsTable(
            self.select_rows,
            self.updates,
            self.update_error,
        )


class _FakeResponse:
    def __init__(self, payload: dict | None = None) -> None:
        self.status_code = 200
        self._payload = payload or {"id": "msg-123"}
        self.text = '{"id":"msg-123"}'
        self.headers: dict[str, str] = {}

    def json(self) -> dict:
        return self._payload


def _submission_row(
    *,
    summary_email_sent: bool = False,
    summary_email_message_id: str | None = None,
    edit_token: str = "edit-123",
) -> dict:
    return {
        "id": "sub-123",
        "summary_email_sent": summary_email_sent,
        "summary_email_message_id": summary_email_message_id,
        "edit_token": edit_token,
    }


def _payload() -> SimpleNamespace:
    return SimpleNamespace(
        workspace_slug="atabaque",
        workflow_type="release_intake",
        meta=SimpleNamespace(form_version="legacy_v1"),
        identification=SimpleNamespace(
            submitter_name="Ana",
            submitter_email="ana@example.com",
            project_title="Projeto Teste",
            release_type="single",
        ),
        project=SimpleNamespace(
            genre="pop",
            release_date="2026-05-01",
        ),
        marketing=SimpleNamespace(focus_track_name=None),
        tracks=[
            SimpleNamespace(
                title="Faixa 1",
                is_focus_track=True,
                primary_artists="Ana",
            )
        ],
    )


class SubmissionSummaryEmailTests(unittest.TestCase):
    def test_send_and_persist_flag(self) -> None:
        updates: list[dict] = []
        payload = _payload()

        with (
            patch.object(
                submissions_module,
                "_load_workspace_email_settings",
                return_value={
                    "workspace_name": "Atabaque",
                    "submission_email_enabled": True,
                    "notification_emails": ["labels@atabaque.biz"],
                },
            ),
            patch.object(
                submissions_module,
                "send_submission_summary_email",
                return_value={"provider_message_id": "msg-123"},
            ) as email_mock,
            patch.object(
                submissions_module,
                "_is_release_intake_payload",
                return_value=True,
            ),
            patch.object(
                submissions_module,
                "supabase",
                _FakeSupabase(
                    [_submission_row(), _submission_row()],
                    updates,
                ),
            ),
            patch.object(
                submissions_module,
                "_utc_now_iso",
                return_value="2026-04-17T22:00:00+00:00",
            ),
        ):
            result = submissions_module._maybe_send_submission_summary_email(
                "sub-123",
                payload,
            )

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["message_id"], "msg-123")
        self.assertEqual(result["recipients_count"], 1)
        self.assertEqual(
            email_mock.call_args.kwargs["idempotency_key"],
            "sub-123:summary",
        )
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["eq_key"], "id")
        self.assertEqual(updates[0]["eq_value"], "sub-123")
        self.assertEqual(
            updates[0]["payload"]["summary_email_sent_at"],
            "2026-04-17T22:00:00+00:00",
        )
        self.assertTrue(updates[0]["payload"]["summary_email_sent"])
        self.assertEqual(
            updates[0]["payload"]["summary_email_message_id"],
            "msg-123",
        )

    def test_short_circuit_when_flag_already_true(self) -> None:
        updates: list[dict] = []

        with (
            patch.object(
                submissions_module,
                "_load_workspace_email_settings",
            ) as settings_mock,
            patch.object(
                submissions_module,
                "send_submission_summary_email",
            ) as email_mock,
            patch.object(
                submissions_module,
                "supabase",
                _FakeSupabase(
                    [_submission_row(summary_email_sent=True, summary_email_message_id="msg-existing")],
                    updates,
                ),
            ),
        ):
            result = submissions_module._maybe_send_submission_summary_email(
                "sub-123",
                _payload(),
            )

        self.assertEqual(result["status"], "already_sent")
        self.assertEqual(result["message_id"], "msg-existing")
        settings_mock.assert_not_called()
        email_mock.assert_not_called()
        self.assertEqual(updates, [])

    def test_returns_already_sent_by_other_on_parallel_write(self) -> None:
        updates: list[dict] = []

        with (
            patch.object(
                submissions_module,
                "_load_workspace_email_settings",
                return_value={
                    "workspace_name": "Atabaque",
                    "submission_email_enabled": True,
                    "notification_emails": ["labels@atabaque.biz"],
                },
            ),
            patch.object(
                submissions_module,
                "send_submission_summary_email",
                return_value={"provider_message_id": "msg-123"},
            ),
            patch.object(
                submissions_module,
                "_is_release_intake_payload",
                return_value=True,
            ),
            patch.object(
                submissions_module,
                "supabase",
                _FakeSupabase(
                    [
                        _submission_row(),
                        _submission_row(
                            summary_email_sent=True,
                            summary_email_message_id="msg-race",
                        ),
                    ],
                    updates,
                ),
            ),
        ):
            result = submissions_module._maybe_send_submission_summary_email(
                "sub-123",
                _payload(),
            )

        self.assertEqual(result["status"], "already_sent_by_other")
        self.assertEqual(result["message_id"], "msg-race")
        self.assertEqual(updates, [])

    def test_skips_when_not_release_intake(self) -> None:
        updates: list[dict] = []

        with (
            patch.object(
                submissions_module,
                "_load_workspace_email_settings",
                return_value={
                    "workspace_name": "Atabaque",
                    "submission_email_enabled": True,
                    "notification_emails": ["labels@atabaque.biz"],
                },
            ) as settings_mock,
            patch.object(
                submissions_module,
                "_is_release_intake_payload",
                return_value=False,
            ),
            patch.object(
                submissions_module,
                "send_submission_summary_email",
            ) as email_mock,
            patch.object(
                submissions_module,
                "supabase",
                _FakeSupabase([_submission_row()], updates),
            ),
        ):
            result = submissions_module._maybe_send_submission_summary_email(
                "sub-123",
                _payload(),
            )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "not_release_intake")
        settings_mock.assert_called_once()
        email_mock.assert_not_called()
        self.assertEqual(updates, [])

    def test_returns_disabled_when_workspace_email_is_disabled(self) -> None:
        updates: list[dict] = []

        with (
            patch.object(
                submissions_module,
                "_load_workspace_email_settings",
                return_value={
                    "workspace_name": "Atabaque",
                    "submission_email_enabled": False,
                    "notification_emails": ["labels@atabaque.biz"],
                },
            ),
            patch.object(
                submissions_module,
                "send_submission_summary_email",
            ) as email_mock,
            patch.object(
                submissions_module,
                "_is_release_intake_payload",
                return_value=True,
            ),
            patch.object(
                submissions_module,
                "supabase",
                _FakeSupabase([_submission_row()], updates),
            ),
        ):
            result = submissions_module._maybe_send_submission_summary_email(
                "sub-123",
                _payload(),
            )

        self.assertEqual(result["status"], "disabled")
        email_mock.assert_not_called()
        self.assertEqual(updates, [])

    def test_skips_when_notification_emails_are_empty(self) -> None:
        updates: list[dict] = []

        with (
            patch.object(
                submissions_module,
                "_load_workspace_email_settings",
                return_value={
                    "workspace_name": "Atabaque",
                    "submission_email_enabled": True,
                    "notification_emails": [],
                },
            ),
            patch.object(
                submissions_module,
                "send_submission_summary_email",
            ) as email_mock,
            patch.object(
                submissions_module,
                "_is_release_intake_payload",
                return_value=True,
            ),
            patch.object(
                submissions_module,
                "supabase",
                _FakeSupabase([_submission_row()], updates),
            ),
        ):
            result = submissions_module._maybe_send_submission_summary_email(
                "sub-123",
                _payload(),
            )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_recipients")
        email_mock.assert_not_called()
        self.assertEqual(updates, [])

    def test_treats_missing_message_id_as_success(self) -> None:
        updates: list[dict] = []

        with (
            patch.object(
                submissions_module,
                "_load_workspace_email_settings",
                return_value={
                    "workspace_name": "Atabaque",
                    "submission_email_enabled": True,
                    "notification_emails": ["labels@atabaque.biz"],
                },
            ),
            patch.object(
                submissions_module,
                "send_submission_summary_email",
                return_value={"provider_response": {"accepted": True}},
            ),
            patch.object(
                submissions_module,
                "_is_release_intake_payload",
                return_value=True,
            ),
            patch.object(
                submissions_module,
                "supabase",
                _FakeSupabase(
                    [_submission_row(), _submission_row()],
                    updates,
                ),
            ),
            patch.object(
                submissions_module,
                "_utc_now_iso",
                return_value="2026-04-17T22:00:00+00:00",
            ),
        ):
            result = submissions_module._maybe_send_submission_summary_email(
                "sub-123",
                _payload(),
            )

        self.assertEqual(result["status"], "sent")
        self.assertIsNone(result["message_id"])
        self.assertEqual(updates[0]["payload"]["summary_email_message_id"], None)
        self.assertTrue(updates[0]["payload"]["summary_email_sent"])

    def test_returns_sent_but_flag_failed_when_update_errors(self) -> None:
        updates: list[dict] = []

        with (
            patch.object(
                submissions_module,
                "_load_workspace_email_settings",
                return_value={
                    "workspace_name": "Atabaque",
                    "submission_email_enabled": True,
                    "notification_emails": ["labels@atabaque.biz"],
                },
            ),
            patch.object(
                submissions_module,
                "send_submission_summary_email",
                return_value={"provider_message_id": "msg-123"},
            ),
            patch.object(
                submissions_module,
                "_is_release_intake_payload",
                return_value=True,
            ),
            patch.object(
                submissions_module,
                "supabase",
                _FakeSupabase(
                    [_submission_row(), _submission_row()],
                    updates,
                    update_error=RuntimeError("db down"),
                ),
            ),
        ):
            result = submissions_module._maybe_send_submission_summary_email(
                "sub-123",
                _payload(),
            )

        self.assertEqual(result["status"], "sent_but_flag_failed")
        self.assertEqual(result["message_id"], "msg-123")
        self.assertEqual(updates, [])


class SendSubmissionSummaryEmailTests(unittest.TestCase):
    def test_forwards_idempotency_key_to_resend(self) -> None:
        with (
            patch.object(email_module.settings, "RESEND_API_KEY", "key-123"),
            patch.object(email_module.settings, "RESEND_FROM_EMAIL", "ops@sunbeat.co"),
            patch.object(
                email_module.requests,
                "post",
                return_value=_FakeResponse(),
            ) as post_mock,
        ):
            result = email_module.send_submission_summary_email(
                to_emails=["labels@atabaque.biz"],
                workspace_name="Atabaque",
                submitter_name="Ana",
                submitter_email="ana@example.com",
                project_title="Projeto Teste",
                release_type="single",
                release_date="2026-05-01",
                genre="pop",
                focus_track_name="Faixa 1",
                track_titles=["Faixa 1"],
                edit_url="https://app.sunbeat.co/intake/atabaque?edit_token=edit-123",
                idempotency_key="sub-123:summary",
            )

        self.assertEqual(result["provider_message_id"], "msg-123")
        self.assertEqual(
            post_mock.call_args.kwargs["headers"]["Idempotency-Key"],
            "sub-123:summary",
        )


if __name__ == "__main__":
    unittest.main()
