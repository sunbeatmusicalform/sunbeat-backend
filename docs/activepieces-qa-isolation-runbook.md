# Activepieces QA isolation gate

This runbook prevents a QA hostname from being mistaken for an isolated QA
database. It must be completed before applying the automation outbox migration
or enabling Activepieces delivery.

## Baseline audited on 2026-08-22

- The Supabase account exposes one project: `sunbeat-core`.
- `sunbeat-market-readiness-qa` and `sunbeat-backend` have matching digests for
  `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.
- Therefore the Fly QA app currently uses the production Supabase project.
- No Fly app identifiable as Activepieces exists in the organization.
- No local Activepieces container is running.
- No Activepieces secret is configured on the Fly QA app.

**Decision:** do not link the repository to `sunbeat-core`, do not run
`supabase db push`, do not add Activepieces secrets to Fly and do not dispatch
events. The production database and Atabaque remain untouched.

That decision applied until an isolated, explicitly approved environment was
available. The one-time QA drill below satisfied that gate without changing
the production project.

## Single manual gate

Provision or explicitly approve one isolated QA automation environment and
provide its connection details through the normal secret managers:

1. a dedicated Supabase QA project or database branch whose project ref is
   different from `sunbeat-core`; and
2. a public HTTPS Activepieces webhook dedicated to the QA flow, together with
   its signing secret.

This is treated as one infrastructure gate. It may create provider cost, so it
requires an explicit cost and provisioning decision before Codex creates or
activates resources.

## Read-only preflight

Do not print secret values. Compare only resource identifiers, secret names and
provider-generated digests.

```bash
supabase projects list --output json
fly status --app sunbeat-market-readiness-qa --json
fly secrets list --app sunbeat-market-readiness-qa --json
fly secrets list --app sunbeat-backend --json
fly apps list --json
```

Pass conditions:

- the QA Supabase project ref differs from production;
- the QA Fly app's Supabase secret digests differ from production;
- the Activepieces webhook is HTTPS and belongs only to the QA flow;
- the allowlist contains only the isolated QA workspace slug;
- `atabaque` remains in the denylist and is also blocked in code.

## Migration sequence after the gate passes

Link and inspect the isolated QA project only. Never substitute the production
project ref in these commands.

```bash
supabase link --project-ref <dedicated-qa-project-ref>
supabase migration list --linked
supabase db push --linked --dry-run
```

The dry-run must show the reviewed migration
`20260822042144_activepieces_automation_outbox.sql`. After human review, apply
it to QA:

```bash
supabase db push --linked
```

Verify in SQL that anonymous and authenticated roles cannot access the outbox:

```sql
select relrowsecurity
from pg_class
where oid = 'public.automation_outbox'::regclass;

select grantee, privilege_type
from information_schema.role_table_grants
where table_schema = 'public'
  and table_name = 'automation_outbox';
```

Expected result: RLS enabled and table privileges granted only to the backend
service role, not to `anon` or `authenticated`.

## QA-only backend configuration

Set these secrets only on the isolated QA Fly app. Adding secrets may restart
that QA app, so record the release before and after the change.

```text
ACTIVEPIECES_ENABLED=true
ACTIVEPIECES_WEBHOOK_URL=<qa-https-webhook>
ACTIVEPIECES_WEBHOOK_SECRET=<secret-manager-value>
ACTIVEPIECES_WORKSPACE_ALLOWLIST=sunbeat-qa-20260808-1108
ACTIVEPIECES_WORKSPACE_DENYLIST=atabaque
```

Do not configure these values on `sunbeat-backend` during the pilot.

## Acceptance evidence

1. Submit only through `sunbeat-qa-20260808-1108`.
2. Confirm one pending outbox row with the QA workspace slug and no raw form
   payload, email, upload URL or authentication token.
3. Call the dispatcher with `dry_run=true` and confirm zero delivery.
4. Enable the QA Activepieces flow and call with `dry_run=false`.
5. Verify the HMAC signature and one successful execution in Activepieces.
6. Repeat the same dispatch and confirm no second delivery.
7. Confirm an `atabaque` dispatch request is blocked and creates no event.
8. Record Fly release, Supabase project ref, event id and timestamps without
   copying secrets into the evidence document.

There is no scheduler or production activation in this pilot. Those require a
separate approval after the QA evidence is complete.

## Completed QA drill — 2026-08-22

The temporary environment was created, tested and removed in the same session.
No production migration, production deployment or Atabaque write was made.

### Isolated resources

- Supabase preview branch: `qa-activepieces-e2e-20260822`
- Preview project ref: `yancvakcdkfiraubzeyi`
- Branch id: `5163c12c-6f94-4ec3-8fbc-d91f0e66303b`
- Fly app: `sunbeat-market-readiness-qa`
- QA workspace allowlisted: `sunbeat-qa-20260808-1108`
- Workspace denied in environment and code: `atabaque`

The branch was created without production data. Its Supabase URL and service
role digest differed from the production values before any migration or event
was created.

### Migration and access evidence

The dry-run listed exactly the five reviewed repository migrations, ending in
`20260822042144_activepieces_automation_outbox.sql`. The push completed only on
the preview ref. SQL verification then confirmed:

- RLS enabled on `public.automation_outbox`;
- table privileges only for `postgres` and `service_role`;
- no policy granting `anon` or `authenticated` access;
- the service-role-only `claim_automation_outbox` RPC installed.

### End-to-end evidence

Synthetic entity: `qa-activepieces-e2e-20260822065044`

Outbox event: `2504b35e-24a0-4aa3-983a-f693fa6b425a`

The payload contained only the synthetic submission id, workflow and form
versions, a QA project title, release date/type, track count and source. It did
not contain an email, upload URL, authentication token or raw form payload.

Observed sequence:

1. The first enqueue produced one pending row.
2. Enqueuing the same entity again returned the same event id with
   `replayed=true`; no duplicate row was created.
3. Enqueuing the same synthetic entity for `atabaque` returned `disabled`.
4. The internal QA status returned `ready`; Atabaque returned `blocked` with
   reason `workspace_denied`.
5. `dry_run=true` returned one candidate and `claimed=0`; the row remained
   pending.
6. The real dispatch returned `claimed=1` and `delivered`.
7. Repeating the real dispatch returned `claimed=0`.
8. The final outbox row had `attempts=1`, `status=delivered`, a delivery
   timestamp and no error.
9. The branch contained zero rows with `workspace_slug=atabaque`.
10. Activepieces recorded the matching latest execution as `Succeeded` at
    03:53 America/Recife. Separate signed-deny and invalid-signature probes had
    already failed as expected, while a valid signed QA probe succeeded.

### Restore and disposal evidence

- The QA Fly app was restored to the original production Supabase URL and
  service-role digests used by QA before the drill.
- All five temporary `ACTIVEPIECES_*` secrets were removed from the QA app.
- Fly release 16 started successfully with both health and readiness checks
  passing.
- The backend suite passed with `239 passed` after the documentation update.
- The preview branch was deleted at `2026-08-22T06:58:55Z`.
- A final branch listing returned only `main` (`pjawmgcnccrdcpjmworg`).

The branch existed for approximately 24.55 minutes. At the documented preview
Micro rate of US$0.01344/hour, estimated compute cost was approximately
US$0.0055, below the approved US$0.25 ceiling. Provider billing may round or
add taxes, so this value is an estimate rather than an invoice.

### Residual gate

The backend outbox is durable and idempotent. The current Activepieces verifier
checks freshness, workspace binding, event-id consistency and HMAC before it
returns success, but it does not yet persist event ids inside Activepieces.
Before adding any downstream side effect, the flow must use a durable event-id
store (or another atomic idempotency guard) and pass a second isolated replay
test. Production migration, scheduling and activation remain separate approval
gates.
