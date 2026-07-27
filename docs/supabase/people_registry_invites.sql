create table if not exists public.people_registry_invites (
    token text primary key,
    workspace_slug text not null,
    profile text not null,
    status text not null default 'pending'
        check (
            status in (
                'pending',
                'sent',
                'opened',
                'submitted',
                'submitted_pending_airtable',
                'failed',
                'expired',
                'discontinued'
            )
        ),
    airtable_clearance_part_id text null,
    context jsonb not null default '{}'::jsonb,
    people_registry_record_id uuid null
        references public.people_registry_records (id)
        on delete set null,
    people_airtable_record_id text null,
    last_error text null,
    expires_at timestamptz null,
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now()),
    opened_at timestamptz null,
    submitted_at timestamptz null
);

alter table public.people_registry_invites
    alter column airtable_clearance_part_id drop not null;

do $$
declare
    constraint_name text;
begin
    for constraint_name in
        select c.conname
        from pg_constraint c
        join pg_attribute a
            on a.attrelid = c.conrelid
            and a.attnum = any (c.conkey)
        where c.conrelid = 'public.people_registry_invites'::regclass
          and c.contype = 'c'
          and a.attname = 'status'
    loop
        execute format(
            'alter table public.people_registry_invites drop constraint %I',
            constraint_name
        );
    end loop;
end $$;

alter table public.people_registry_invites
    add constraint people_registry_invites_status_check
    check (
        status in (
            'pending',
            'sent',
            'opened',
            'submitted',
            'submitted_pending_airtable',
            'failed',
            'expired',
            'discontinued'
        )
    );

alter table public.people_registry_records
    add column if not exists edit_token text null;

create unique index if not exists idx_people_registry_records_edit_token
    on public.people_registry_records (edit_token)
    where edit_token is not null;

create index if not exists idx_people_registry_invites_workspace_status
    on public.people_registry_invites (workspace_slug, status);

create index if not exists idx_people_registry_invites_clearance_part
    on public.people_registry_invites (airtable_clearance_part_id);

create index if not exists idx_people_registry_invites_people_record
    on public.people_registry_invites (people_registry_record_id);

-- Convites são acessados somente pelo backend. O link público chama a API
-- FastAPI por token; o browser não consulta esta tabela pelo Data API.
alter table public.people_registry_invites enable row level security;

revoke all privileges on table public.people_registry_invites
    from anon, authenticated;

grant select, insert, update, delete
    on table public.people_registry_invites
    to service_role;
