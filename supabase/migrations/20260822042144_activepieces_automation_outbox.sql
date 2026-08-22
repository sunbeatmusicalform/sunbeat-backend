-- Durable, tenant-scoped outbox for optional external automation delivery.
-- No client-side policies are created: only the backend service role can read
-- or mutate these events. Applying this migration does not enable delivery.

create table if not exists public.automation_outbox (
    id uuid primary key default gen_random_uuid(),
    workspace_slug text not null,
    event_type text not null,
    entity_type text not null,
    entity_id text not null,
    idempotency_key text not null unique,
    payload jsonb not null default '{}'::jsonb,
    status text not null default 'pending'
        check (status in ('pending', 'sending', 'delivered', 'failed', 'dead_letter')),
    attempts integer not null default 0 check (attempts >= 0),
    next_attempt_at timestamptz not null default now(),
    locked_at timestamptz,
    worker_id text,
    last_error text,
    delivered_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists automation_outbox_due_idx
    on public.automation_outbox (status, next_attempt_at, created_at);

create index if not exists automation_outbox_workspace_idx
    on public.automation_outbox (workspace_slug, created_at desc);

alter table public.automation_outbox enable row level security;

revoke all on table public.automation_outbox from anon, authenticated;
grant all on table public.automation_outbox to service_role;

create or replace function public.claim_automation_outbox(
    p_worker_id text,
    p_workspace_slug text,
    p_limit integer default 25
)
returns setof public.automation_outbox
language plpgsql
security definer
set search_path = public
as $$
begin
    if nullif(trim(p_workspace_slug), '') is null then
        raise exception 'workspace slug is required';
    end if;

    return query
    with due as (
        select id
        from public.automation_outbox
        where (
            status in ('pending', 'failed')
            or (status = 'sending' and locked_at < now() - interval '15 minutes')
        )
          and next_attempt_at <= now()
          and workspace_slug = lower(trim(p_workspace_slug))
        order by created_at asc
        for update skip locked
        limit greatest(1, least(coalesce(p_limit, 25), 100))
    )
    update public.automation_outbox as events
       set status = 'sending',
           attempts = events.attempts + 1,
           locked_at = now(),
           worker_id = p_worker_id,
           updated_at = now()
      from due
     where events.id = due.id
    returning events.*;
end;
$$;

revoke all on function public.claim_automation_outbox(text, text, integer) from public, anon, authenticated;
grant execute on function public.claim_automation_outbox(text, text, integer) to service_role;
