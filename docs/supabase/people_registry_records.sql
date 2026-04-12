create extension if not exists pgcrypto;

create table if not exists public.people_registry_records (
    id uuid primary key default gen_random_uuid(),
    workspace_slug text not null,
    workflow_type text not null,
    form_version text not null,
    profile text not null,
    source text null,
    party_kind text not null check (party_kind in ('pf', 'pj')),
    display_name text not null,
    legal_name text not null,
    stage_name text null,
    trade_name text null,
    document_id text null,
    email_primary text null,
    phone_primary text null,
    country text null,
    state_region text null,
    city text null,
    roles_json jsonb not null default '[]'::jsonb,
    payload jsonb not null,
    airtable_sync_status text not null default 'pending'
        check (airtable_sync_status in ('pending', 'blocked', 'failed', 'synced')),
    airtable_sync_error text null,
    airtable_base_id text null,
    airtable_table_name text null,
    airtable_record_id text null,
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_people_registry_records_workspace
    on public.people_registry_records (workspace_slug);

create index if not exists idx_people_registry_records_workflow
    on public.people_registry_records (workspace_slug, workflow_type, form_version, profile);

create index if not exists idx_people_registry_records_document
    on public.people_registry_records (workspace_slug, document_id);

create index if not exists idx_people_registry_records_email
    on public.people_registry_records (workspace_slug, email_primary);

create index if not exists idx_people_registry_records_airtable_status
    on public.people_registry_records (airtable_sync_status);
