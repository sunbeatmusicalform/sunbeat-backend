-- Close legacy Data API exposure without changing the server-side access model.
--
-- These tables are owned by the Fly backend. Browser roles must not read or
-- mutate them directly; the backend uses the service_role key and remains the
-- only application data path. The existence checks keep the migration usable
-- in fresh local environments whose historical baseline is still incomplete.

do $migration$
declare
  table_name text;
  server_only_tables constant text[] := array[
    'submissions_revisions',
    'ai_usage_log',
    'release_intake_drafts',
    'workspace_airtable_mapping',
    'workspace_field_overrides',
    'workspace_branding',
    'workspace_plan_overrides',
    'workspace_workflow_settings'
  ];
begin
  foreach table_name in array server_only_tables loop
    if pg_catalog.to_regclass(pg_catalog.format('public.%I', table_name)) is not null then
      execute pg_catalog.format(
        'alter table public.%I enable row level security',
        table_name
      );
      execute pg_catalog.format(
        'revoke all privileges on table public.%I from public, anon, authenticated',
        table_name
      );
      execute pg_catalog.format(
        'grant select, insert, update, delete on table public.%I to service_role',
        table_name
      );
    end if;
  end loop;
end;
$migration$;

-- Trigger helpers only call pg_catalog functions, so an empty search_path is
-- safe and prevents object-shadowing through mutable schemas.
do $migration$
begin
  if pg_catalog.to_regprocedure('public.set_updated_at()') is not null then
    execute 'alter function public.set_updated_at() set search_path = ''''';
  end if;

  if pg_catalog.to_regprocedure('public.touch_setup_ai_config_drafts_updated_at()') is not null then
    execute 'alter function public.touch_setup_ai_config_drafts_updated_at() set search_path = ''''';
  end if;
end;
$migration$;
