-- Preserve audit metadata after Free-plan Storage object deletion.

create table if not exists public.asset_retention_records (
  id uuid primary key,
  workspace_slug text not null,
  draft_token_hash text not null,
  storage_bucket text not null,
  storage_path text not null,
  file_name text,
  mime_type text,
  size_bytes bigint not null default 0,
  content_sha256 text,
  retention_days integer not null check (retention_days > 0),
  expires_at timestamptz not null,
  storage_status text not null check (storage_status in ('pending_upload', 'uploaded', 'deleted', 'missing', 'error')),
  deletion_attempts integer not null default 0,
  last_error text,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (storage_bucket, storage_path)
);

create index if not exists asset_retention_due_idx
  on public.asset_retention_records (expires_at)
  where deleted_at is null;

create index if not exists asset_retention_workspace_idx
  on public.asset_retention_records (workspace_slug, created_at desc);

alter table public.asset_retention_records enable row level security;
revoke all on table public.asset_retention_records from anon, authenticated;
grant select, insert, update, delete on table public.asset_retention_records to service_role;
