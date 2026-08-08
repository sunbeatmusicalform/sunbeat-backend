# Sunbeat production-readiness runbook

Status captured for the market-readiness branch on 2026-08-07. Production is
Fly.io only. This document does not authorize a deploy, migration, schedule,
DNS change, or write against a customer workspace.

## Release gate

Before a future deploy, in this order:

1. Review and apply `docs/supabase/self_service_security.sql` with a privileged
   migration identity. Confirm that `anon` and `authenticated` cannot read the
   four new tables and that only `service_role` can execute the two functions.
   The functions are `security invoker`, so the explicit service-role table
   grants remain the authority boundary.
2. Review and apply `docs/supabase/asset_retention.sql`.
3. Create a new QA-only self-service workspace through `/signup`; never use
   `atabaque` for onboarding E2E.
4. Exercise signup, first magic-link use, replay rejection, onboarding preview,
   apply, refresh, logout, and post-logout rejection on both public domains.
5. Run the retention command without `--apply` and retain its JSON logs. Do not
   schedule or run `--apply` until a QA object older than the cutoff has been
   observed and restored from backup.
6. Deploy only after Felipe explicitly approves the PR, migrations, and release.

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

Current status: **restore drill not executed or evidenced**. Do not describe
backup as tested until all six steps have evidence.

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
