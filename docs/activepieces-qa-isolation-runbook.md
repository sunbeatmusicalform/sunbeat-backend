# Activepieces QA isolation gate

This runbook prevents a QA hostname from being mistaken for an isolated QA
database. It must be completed before applying the automation outbox migration
or enabling Activepieces delivery.

## Current state audited on 2026-08-22

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
