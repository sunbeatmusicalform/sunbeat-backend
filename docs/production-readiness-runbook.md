# Sunbeat production-readiness runbook

Status captured for the market-readiness branch on 2026-08-07. Production is
Fly.io only. This document does not authorize a deploy, migration, schedule,
DNS change, or write against a customer workspace.

## Release gate

Before a future deploy, in this order:

1. Confirm Supabase migration history still contains
   `20260808125636_self_service_security.sql` and
   `20260808125637_asset_retention.sql`, applied with Felipe's authorization on
   2026-08-08. Recheck that `anon` and `authenticated` cannot read the five new
   tables and that only `service_role` can execute the two functions. The
   functions are `security invoker`, so the explicit service-role table grants
   remain the authority boundary.
2. Re-run Supabase security/performance advisors and record any delta. Do not
   enable RLS on a legacy operational table until its required policies and
   application compatibility have been reviewed.
3. Create a new QA-only self-service workspace through `/signup`; never use
   `atabaque` for onboarding E2E.
4. Exercise signup, first magic-link use, replay rejection, onboarding preview,
   apply, refresh, logout, and post-logout rejection on both public domains.
5. Run the retention command without `--apply` and retain its JSON logs. Do not
   schedule or run `--apply` until a QA object older than the cutoff has been
   observed and restored from backup.
6. Deploy only after Felipe explicitly approves the PR and Fly release.

Rollback the application release on Fly if readiness fails. Schema additions
are backward-compatible and should be retained during an application rollback;
dropping them would destroy security/audit history.

## Health, readiness, and 5xx

- `/health` is liveness only and does not call dependencies.
- `/readiness` requires the explicit service-role secret when self-service is
  enabled, checks that the application can query Supabase, and read-checks all
  migration-owned tables. It returns 503 without connection details when the
  configuration, database, or required schema is unavailable.
- Every response receives `X-Request-ID`; 5xx logs include request ID, method,
  path, and status. Alert routing and thresholds still require an operational
  decision/provider configuration. No alert is claimed active by this branch.

Suggested initial alert: more than 2% 5xx over five minutes, or three consecutive
readiness failures. Route it to a monitored channel before declaring this item
complete.

The branch includes `.github/workflows/production-health-monitor.yml` as a
no-new-vendor baseline. After merge, it checks both public domains and backend
readiness every six hours, and a failed run uses GitHub's existing workflow
notification path. The deliberately low frequency limits included Actions
usage; it is not real-time monitoring, does not calculate a 5xx rate, and does
not replace a dedicated alert provider. Run it manually once after the approved
production deploy to establish the first successful result. It must not be
enabled by merging this branch before Felipe approves the release.

## Backup and restore

Supabase database backups and Storage object recovery must be verified in the
actual project dashboard. Repository access alone cannot prove either one.

Restore drill (QA only):

1. Record the project backup/PITR configuration and retention window.
2. Create a QA workspace, one draft, one asset, and one audit row with a unique
   drill ID.
3. Take or identify a backup after those writes.
4. Restore into an isolated project, never over production.
5. Verify workspace metadata, draft metadata, auth-security rows, audit rows,
   retention metadata, and the QA object bytes/checksum.
6. Record timestamps, RPO, RTO, operator, evidence links, and cleanup.

Current status: eight completed daily physical backups were listed read-only on
2026-08-08 (2026-08-01 through 2026-08-08), WAL-G is active, and PITR is
disabled. **The restore drill was not executed or evidenced.** Do not describe
backup as restore-tested until all six steps have evidence, and never run the
CLI restore command against the real project for this drill.

On 2026-08-08 Felipe accepted deferring the isolated restore clone because it
would add compute cost while budget is unavailable. No clone was created and no
restore command was run. This is an explicit residual risk, not a successful
restore test; revisit it when a temporary isolated project's cost is approved.

## Free asset retention

`scripts/enforce_free_asset_retention.py` is dry-run by default. It selects only
expired, not-yet-deleted registry rows. `--apply` deletes the Storage object and
then preserves the registry row, checksum/size/name metadata, deletion time,
attempt count, and result. Missing objects are recorded as `missing`; failures
remain eligible for an idempotent retry. Access returns HTTP 410 after expiry
even if physical cleanup has not run yet.

There is intentionally no production schedule in this change. The single
manual activation step is to create a Fly schedule/cron only after the QA dry
run, QA deletion, and restore drill are approved. Suggested command:

```text
python -m scripts.enforce_free_asset_retention --apply --limit 100
```

The first real dry-run was executed from the isolated Fly QA app on 2026-08-08
without `--apply`: zero eligible, deleted, missing, or failed records. This
proves the read-only operational path but does not authorize the apply command
or a schedule.

## Security and secrets

- Application secrets belong in Fly secrets/Supabase configuration, never in
  the repository or frontend bundle.
- Rotate `INTERNAL_ADMIN_TOKEN` through a coordinated session invalidation
  window; it signs existing portal, magic-link, and onboarding-preview tokens.
- CORS is restricted to current Sunbeat/Fly origins and explicit methods and
  headers. Authentication uses a request header and browser `sessionStorage`,
  not cookies; therefore cookie `SameSite`/`Secure` flags are not applicable to
  the current portal session.
- Magic links are hashed at rest, bound to user and workspace, consumed
  atomically once, and expire after 30 minutes. Self-service portal sessions are
  checked against membership and a revocable persistent session row.
- The `self_service` authorization marker and retention policy live in
  server-controlled Supabase `app_metadata`; user-editable `user_metadata` is
  never accepted for authorization.
- Managed password sessions remain stateless for compatibility. Migrating them
  to per-user sessions is a residual risk and must not be done by changing a
  real client's workflow configuration.
- The 2026-08-08 Supabase advisor run still reports pre-existing public tables
  without RLS and other legacy findings. Treat these as an open security work
  item; enabling RLS without complete policies could break production access.

## LGPD, Terms, and Privacy checklist

- [x] Signup stores acceptance timestamp and version identifiers.
- [x] Public lead purpose is stated in the form and delivery is persisted.
- [x] Rate-limit identifiers and draft tokens are stored as keyed/hash values,
      not raw identifiers.
- [x] Free asset deletion preserves operational metadata and audit history.
- [ ] Felipe/legal must approve and publish the actual Terms of Use and Privacy
      Policy at stable URLs in English and Portuguese. The signup currently
      names them but has no published document routes in this repository.
- [ ] Define data-controller identity, DPO/contact channel, lawful bases,
      subprocessors (Fly.io, Supabase, Resend, Airtable, Google), international
      transfer language, data-subject request process, and retention periods for
      leads, auth logs, submissions, and audit logs.
- [ ] Execute and evidence a deletion/access request in an isolated QA tenant.

## QA E2E boundary

Automated tests use in-memory fakes and tenant-isolation assertions. The only
manual E2E step requiring credentials is creation of a disposable QA user and
workspace plus inbox access for its magic link. No production customer account
or Atabaque write is needed or permitted.
