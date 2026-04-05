create table if not exists public.file_access_stats (
  file_key text primary key,
  storage_bucket text not null,
  storage_path text not null,
  file_name text,
  mime_type text,
  access_count bigint not null default 0,
  download_count bigint not null default 0,
  last_accessed_at timestamptz,
  last_downloaded_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists file_access_stats_bucket_path_idx
  on public.file_access_stats (storage_bucket, storage_path);
