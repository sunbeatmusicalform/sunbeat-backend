create table if not exists public.setup_ai_action_audit (
  id uuid primary key default gen_random_uuid(),
  workspace_slug text not null,
  workflow_type text not null,
  surface text not null default 'app_setup_copilot',
  action_type text not null default 'configure_airtable',
  operation text not null check (operation in ('read', 'preview_patch', 'apply_patch')),
  status text not null check (status in ('requested', 'succeeded', 'failed', 'blocked')),
  requested_by_user_id uuid,
  requested_by_email text,
  request_payload jsonb not null default '{}'::jsonb,
  backend_response jsonb,
  error_message text,
  dry_run boolean,
  confirmed boolean,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

alter table public.setup_ai_action_audit enable row level security;

revoke all on table public.setup_ai_action_audit from anon;
revoke all on table public.setup_ai_action_audit from authenticated;

create index if not exists setup_ai_action_audit_workspace_created_idx
  on public.setup_ai_action_audit (workspace_slug, created_at desc);

create index if not exists setup_ai_action_audit_workflow_created_idx
  on public.setup_ai_action_audit (workspace_slug, workflow_type, created_at desc);

create index if not exists setup_ai_action_audit_status_idx
  on public.setup_ai_action_audit (status, created_at desc);
