# Legacy public-table security audit — 2026-08-08

This audit was performed against the linked `sunbeat-core` Supabase project in
read-only mode. It inspected catalog metadata, grants, function definitions,
and Supabase advisors. It did not read customer rows, write any database row,
apply a migration, or query the Atabaque workspace.

## Confirmed P0 findings

The Security Advisor reported `rls_disabled_in_public` for eight tables. A
catalog query confirmed that each table had RLS disabled, no policies, and all
table privileges granted to both `anon` and `authenticated`:

- `submissions_revisions`
- `ai_usage_log`
- `release_intake_drafts`
- `workspace_airtable_mapping`
- `workspace_field_overrides`
- `workspace_branding`
- `workspace_plan_overrides`
- `workspace_workflow_settings`

The advisor also reported mutable `search_path` settings on the trigger
functions `public.set_updated_at()` and
`public.touch_setup_ai_config_drafts_updated_at()`. Both functions are
`security invoker` and only call `now()`, which resolves safely through
`pg_catalog` with an empty search path.

## Compatibility decision

These relations are server-owned. The Fly backend uses the Supabase
`service_role` key, and production readiness fails closed when self-service is
enabled without that key. The Vite source served by Fly has no Supabase client
dependency and does not query these tables directly.

Migration `20260808185409_harden_legacy_public_tables.sql` therefore:

1. enables RLS on every existing table in the list;
2. revokes all table privileges from `PUBLIC`, `anon`, and `authenticated`;
3. preserves explicit SELECT/INSERT/UPDATE/DELETE access for `service_role`;
4. fixes both trigger functions to an empty `search_path`; and
5. uses catalog existence checks so the migration remains idempotent and can
   run in an incomplete local baseline.

No tenant policy is created because browser access is not part of the current
application contract. Adding permissive authenticated policies would recreate
an unnecessary tenant-isolation surface.

## Findings deliberately separated

- Thirteen server-only tables have RLS enabled with no policy. This is an
  informational advisor result and is expected when browser grants are denied.
- Eleven existing RLS policies have initialization-plan performance warnings.
- Two duplicate-index warnings remain.
- Supabase Auth leaked-password protection is disabled. The production
  self-service path uses custom one-time magic links, not Supabase passwords;
  password-auth configuration requires a separate product decision.

These items are not mixed into the P0 exposure patch because they have
different compatibility and operational risk.

## Verification and release gate

Before applying the migration:

1. review and merge its PR explicitly;
2. confirm Fly still has the `SUPABASE_SERVICE_ROLE_KEY` secret name without
   exposing its value;
3. apply the migration through the reviewed Supabase migration workflow; and
4. immediately rerun the Security Advisor, `/readiness`, and public route
   smoke tests.

Expected post-apply evidence is zero `rls_disabled_in_public` results for the
eight tables, zero mutable-search-path results for the two functions, no
`anon`/`authenticated` grants on those tables, and successful service-role
readiness. Do not roll back by restoring browser grants. If readiness fails,
keep the database boundary closed and repair the Fly service-role
configuration before resuming traffic.
