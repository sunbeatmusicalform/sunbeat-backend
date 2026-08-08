# Workspace membership RLS audit — 2026-08-08

This follow-up was read-only and did not query customer rows or perform an
Atabaque write.

## Confirmed exposure

`public.workspace_users` granted every table privilege to both `anon` and
`authenticated`. Its policy `workspace_users_service_insert` targeted
`PUBLIC` and used `WITH CHECK (true)`. Together, those settings allowed a
browser role to attempt arbitrary workspace membership insertion.

No exploit or synthetic insertion was performed. The catalog metadata is
sufficient evidence of the authorization gap.

## Proposed boundary

- `anon`: no table privileges on `workspace_users` or `workspaces`.
- `authenticated`: SELECT only on its own `workspace_users` rows; SELECT on
  member workspaces; UPDATE only where it remains an owner.
- `service_role`: explicit server CRUD, plus its native `BYPASSRLS` behavior.

The hotfix removes the permissive insert policy, scopes policies explicitly to
`authenticated`, caches `auth.uid()` through scalar subqueries, adds the
missing owner `WITH CHECK`, and adds an index beginning with `user_id` for the
membership lookups.

The production Fly application remains compatible because membership creation
and management pass through the backend service-role client. The Vite frontend
served by Fly has no direct Supabase client dependency.

## Release gate

Apply only after PR review and Felipe's explicit approval. Immediately verify:

1. browser roles have no INSERT/UPDATE/DELETE privilege on
   `workspace_users`;
2. the permissive insert policy no longer exists;
3. authenticated policies target only `authenticated` and use
   `(select auth.uid())`;
4. service-role reads/writes and Fly readiness remain healthy; and
5. isolated QA onboarding works without any Atabaque write.
