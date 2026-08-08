-- Prevent browser roles from creating or mutating workspace memberships.
-- The Fly backend is the only membership writer and uses service_role.

do $migration$
begin
  if pg_catalog.to_regclass('public.workspace_users') is not null then
    execute 'alter table public.workspace_users enable row level security';
    execute 'revoke all privileges on table public.workspace_users from public, anon, authenticated';
    execute 'grant select on table public.workspace_users to authenticated';
    execute 'grant select, insert, update, delete on table public.workspace_users to service_role';

    execute 'drop policy if exists workspace_users_service_insert on public.workspace_users';
    execute 'drop policy if exists workspace_users_self_read on public.workspace_users';
    execute $policy$
      create policy workspace_users_self_read
        on public.workspace_users
        for select
        to authenticated
        using (user_id = (select auth.uid()))
    $policy$;

    execute 'create index if not exists workspace_users_user_id_workspace_slug_idx on public.workspace_users (user_id, workspace_slug)';
  end if;

  if pg_catalog.to_regclass('public.workspaces') is not null then
    execute 'alter table public.workspaces enable row level security';
    execute 'revoke all privileges on table public.workspaces from public, anon, authenticated';
    execute 'grant select, update on table public.workspaces to authenticated';
    execute 'grant select, insert, update, delete on table public.workspaces to service_role';

    execute 'drop policy if exists workspaces_member_read on public.workspaces';
    execute $policy$
      create policy workspaces_member_read
        on public.workspaces
        for select
        to authenticated
        using (
          slug in (
            select workspace_slug
            from public.workspace_users
            where user_id = (select auth.uid())
          )
        )
    $policy$;

    execute 'drop policy if exists workspaces_owner_update on public.workspaces';
    execute $policy$
      create policy workspaces_owner_update
        on public.workspaces
        for update
        to authenticated
        using (
          slug in (
            select workspace_slug
            from public.workspace_users
            where user_id = (select auth.uid())
              and role = 'owner'
          )
        )
        with check (
          slug in (
            select workspace_slug
            from public.workspace_users
            where user_id = (select auth.uid())
              and role = 'owner'
          )
        )
    $policy$;
  end if;
end;
$migration$;
