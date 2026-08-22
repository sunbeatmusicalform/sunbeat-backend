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

The Fly app name or hostname is not proof of database isolation. Before any
migration, follow the isolation gate in
[`activepieces-qa-isolation-runbook.md`](activepieces-qa-isolation-runbook.md).

1. Confirm that QA has a dedicated Supabase project or database branch and
   that its project ref differs from production.
2. Run `supabase db push --dry-run` against that QA ref and review the single
   expected migration.
3. Apply the reviewed migration only to the isolated QA database. Do not
   schedule anything yet.
4. Create an isolated Activepieces webhook flow and keep it disabled while
   mapping fields.
5. Configure the backend only for the isolated QA workspace.
6. Inspect due events without delivery: `POST /internal/automations/dispatch?dry_run=true&workspace_slug=<qa-slug>`.
7. Enable the Activepieces flow, then explicitly dispatch: `POST /internal/automations/dispatch?dry_run=false&workspace_slug=<qa-slug>`.
8. Confirm a second call does not redeliver the same event and inspect the
   audit row.

There is intentionally no production scheduler in this PR. Scheduling is a separate approval after the QA replay and restore procedures have evidence.

## QA validation result

The complete isolated drill ran on 2026-08-22 and passed. The temporary
Supabase preview branch received the migration, the Fly QA backend enqueued one
synthetic non-PII event, Activepieces accepted the signed delivery and a second
dispatch claimed zero events. Atabaque remained blocked in code and in the QA
denylist, and the temporary database contained zero Atabaque outbox rows.

The QA app was restored, all temporary Activepieces secrets were removed and
the preview branch was deleted. Full identifiers, timestamps, access checks,
cost estimate and teardown evidence are recorded in
[`activepieces-qa-isolation-runbook.md`](activepieces-qa-isolation-runbook.md).

This validates the transport contract and the backend idempotency boundary. It
does not authorize production activation. Before a flow performs email, Drive,
Airtable or any other external side effect, add persistent deduplication inside
the automation layer and repeat the replay test in isolated QA.
