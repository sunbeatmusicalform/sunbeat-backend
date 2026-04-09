-- Adds explicit workflow scoping to workspace_field_overrides so the
-- internal editor can isolate Release Intake and Rights Clearance safely.
-- Impact:
-- 1. legacy release intake rows are backfilled to release_intake + legacy_v1
-- 2. workspace-level settings/security rows remain global (NULL scope)
-- 3. workflow-config PUT becomes safe for multi-tenant workspaces

alter table public.workspace_field_overrides
  add column if not exists workflow_type text;

alter table public.workspace_field_overrides
  add column if not exists form_version text;

update public.workspace_field_overrides
set workflow_type = 'release_intake'
where workflow_type is null
  and step_key not in ('__workspace_settings__', '__workspace_security__');

update public.workspace_field_overrides
set form_version = 'legacy_v1'
where form_version is null
  and workflow_type = 'release_intake'
  and step_key not in ('__workspace_settings__', '__workspace_security__');

create index if not exists idx_workspace_field_overrides_workspace_scope
  on public.workspace_field_overrides (
    workspace_slug,
    workflow_type,
    form_version,
    step_key,
    field_key
  );
