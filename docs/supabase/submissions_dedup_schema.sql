create extension if not exists pgcrypto;

alter table public.tracks
  add column if not exists client_track_id uuid,
  add column if not exists deleted_at timestamptz;

create unique index if not exists tracks_submission_client_track_uidx
  on public.tracks (submission_id, client_track_id)
  where deleted_at is null;

alter table public.submissions
  add column if not exists google_drive_folder_id text,
  add column if not exists idempotency_key text;

create table if not exists public.submissions_revisions (
  id uuid primary key,
  submission_id uuid not null references public.submissions(id),
  version integer not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create unique index if not exists submissions_revisions_submission_version_uidx
  on public.submissions_revisions (submission_id, version);

create index if not exists submissions_revisions_submission_created_idx
  on public.submissions_revisions (submission_id, created_at desc);
