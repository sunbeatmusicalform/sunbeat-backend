-- Required before deploying the self-service auth hardening release.
-- Safe to run repeatedly. Service-role access only; no browser client access.

create table if not exists public.self_service_magic_links (
  token_id uuid primary key,
  token_hash text not null unique,
  user_id uuid not null,
  workspace_slug text not null,
  purpose text not null check (purpose in ('signup', 'login')),
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists self_service_magic_links_expiry_idx
  on public.self_service_magic_links (expires_at) where consumed_at is null;

create table if not exists public.portal_sessions (
  session_id uuid primary key,
  user_id uuid not null,
  workspace_slug text not null,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists portal_sessions_user_workspace_idx
  on public.portal_sessions (user_id, workspace_slug, created_at desc);

create table if not exists public.public_rate_limits (
  scope text not null,
  identifier_hash text not null,
  window_started_at timestamptz not null,
  attempt_count integer not null check (attempt_count > 0),
  primary key (scope, identifier_hash)
);

create table if not exists public.public_leads (
  id uuid primary key,
  lead_type text not null check (lead_type in ('waitlist', 'enterprise', 'academy')),
  name text not null,
  email text not null,
  company text,
  plan text,
  message text,
  locale text not null check (locale in ('en', 'pt-BR')),
  delivery_status text not null check (delivery_status in ('received', 'delivered', 'failed')),
  provider_message_id text,
  delivery_error text,
  created_at timestamptz not null default now(),
  delivered_at timestamptz
);

create index if not exists public_leads_created_idx
  on public.public_leads (created_at desc);

alter table public.self_service_magic_links enable row level security;
alter table public.portal_sessions enable row level security;
alter table public.public_rate_limits enable row level security;
alter table public.public_leads enable row level security;

revoke all on table public.self_service_magic_links from anon, authenticated;
revoke all on table public.portal_sessions from anon, authenticated;
revoke all on table public.public_rate_limits from anon, authenticated;
revoke all on table public.public_leads from anon, authenticated;
grant select, insert, update, delete on table public.self_service_magic_links to service_role;
grant select, insert, update, delete on table public.portal_sessions to service_role;
grant select, insert, update, delete on table public.public_rate_limits to service_role;
grant select, insert, update, delete on table public.public_leads to service_role;

create or replace function public.consume_self_service_magic_link(
  p_token_id uuid,
  p_token_hash text,
  p_user_id uuid,
  p_workspace_slug text
) returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  changed integer;
begin
  update public.self_service_magic_links
     set consumed_at = pg_catalog.now()
   where token_id = p_token_id
     and token_hash = p_token_hash
     and user_id = p_user_id
     and workspace_slug = p_workspace_slug
     and consumed_at is null
     and expires_at > pg_catalog.now();
  get diagnostics changed = row_count;
  return changed = 1;
end;
$$;

create or replace function public.consume_public_rate_limit(
  p_scope text,
  p_identifier_hash text,
  p_limit integer,
  p_window_seconds integer
) returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  current_count integer;
begin
  if p_limit < 1 or p_window_seconds < 1 then
    return false;
  end if;

  insert into public.public_rate_limits(scope, identifier_hash, window_started_at, attempt_count)
  values (p_scope, p_identifier_hash, pg_catalog.now(), 1)
  on conflict (scope, identifier_hash) do update
    set window_started_at = case
          when public.public_rate_limits.window_started_at <= pg_catalog.now() - pg_catalog.make_interval(secs => p_window_seconds)
          then pg_catalog.now() else public.public_rate_limits.window_started_at end,
        attempt_count = case
          when public.public_rate_limits.window_started_at <= pg_catalog.now() - pg_catalog.make_interval(secs => p_window_seconds)
          then 1 else public.public_rate_limits.attempt_count + 1 end
  returning attempt_count into current_count;

  return current_count <= p_limit;
end;
$$;

revoke all on function public.consume_self_service_magic_link(uuid, text, uuid, text) from public, anon, authenticated;
revoke all on function public.consume_public_rate_limit(text, text, integer, integer) from public, anon, authenticated;
grant execute on function public.consume_self_service_magic_link(uuid, text, uuid, text) to service_role;
grant execute on function public.consume_public_rate_limit(text, text, integer, integer) to service_role;
