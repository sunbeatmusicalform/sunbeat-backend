# Activepieces automation pilot

This integration is an optional delivery layer for tenant-scoped Sunbeat events. It does not move business rules, entitlements, idempotency or tenant authorization out of the Sunbeat backend.

## Safety defaults

- Delivery is off unless `ACTIVEPIECES_ENABLED=true`.
- A workspace must be present in `ACTIVEPIECES_WORKSPACE_ALLOWLIST`.
- `atabaque` is denied by default through `ACTIVEPIECES_WORKSPACE_DENYLIST`.
- The webhook URL must use HTTPS, except for localhost development.
- Events exclude contact email, upload URLs, auth tokens and the raw form payload.
- Every event has a unique idempotency key, is signed with HMAC-SHA256 and is persisted before delivery.
- The dispatcher starts in dry-run mode and requires `X-Admin-Token`.

## Activepieces flow contract

The webhook receives JSON with these stable envelope fields:

```json
{
  "schema_version": "2026-08-22",
  "event_id": "uuid",
  "event_type": "submission.created",
  "workspace_slug": "qa-workspace",
  "entity": {"type": "submission", "id": "uuid"},
  "occurred_at": "ISO-8601",
  "data": {
    "submission_id": "uuid",
    "workflow_type": "release_intake",
    "form_version": "release_intake_v2",
    "project_title": "Example",
    "release_date": "2026-09-18",
    "release_type": "single",
    "track_count": 1,
    "source": "sunbeat"
  }
}
```

Signature headers:

- `Idempotency-Key` and `X-Sunbeat-Event-Id`: the outbox event id.
- `X-Sunbeat-Timestamp`: Unix timestamp.
- `X-Sunbeat-Signature`: `sha256=` followed by HMAC-SHA256 of `<timestamp>.<canonical-json-body>`.

The Activepieces flow must reject stale timestamps, verify the signature with the shared secret and deduplicate by event id before running side effects.

## QA activation and manual dispatch

1. Apply the reviewed Supabase migration. Do not schedule anything yet.
2. Create an isolated Activepieces webhook flow and keep it disabled while mapping fields.
3. Configure the backend only for the isolated QA workspace.
4. Inspect due events without delivery: `POST /internal/automations/dispatch?dry_run=true&workspace_slug=<qa-slug>`.
5. Enable the Activepieces flow, then explicitly dispatch: `POST /internal/automations/dispatch?dry_run=false&workspace_slug=<qa-slug>`.
6. Confirm a second call does not redeliver the same event and inspect the audit row.

There is intentionally no production scheduler in this PR. Scheduling is a separate approval after the QA replay and restore procedures have evidence.
