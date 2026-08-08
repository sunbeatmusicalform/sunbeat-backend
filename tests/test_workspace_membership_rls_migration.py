from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260808192621_harden_workspace_membership_rls.sql"
).read_text(encoding="utf-8").lower()


def test_browser_roles_cannot_write_workspace_memberships() -> None:
    assert "revoke all privileges on table public.workspace_users" in MIGRATION
    assert "grant select on table public.workspace_users to authenticated" in MIGRATION
    assert "grant select, insert, update, delete on table public.workspace_users to service_role" in MIGRATION
    assert "drop policy if exists workspace_users_service_insert" in MIGRATION
    assert "with check (true)" not in MIGRATION


def test_membership_reads_are_bound_to_authenticated_user() -> None:
    assert "create policy workspace_users_self_read" in MIGRATION
    assert "to authenticated" in MIGRATION
    assert "user_id = (select auth.uid())" in MIGRATION
    assert "workspace_users_user_id_workspace_slug_idx" in MIGRATION


def test_workspace_updates_preserve_owner_boundary() -> None:
    assert "create policy workspaces_member_read" in MIGRATION
    assert "create policy workspaces_owner_update" in MIGRATION
    assert "grant select, update on table public.workspaces to authenticated" in MIGRATION
    assert "and role = 'owner'" in MIGRATION
    assert "with check" in MIGRATION


def test_migration_is_safe_for_incomplete_local_baseline() -> None:
    assert "pg_catalog.to_regclass('public.workspace_users')" in MIGRATION
    assert "pg_catalog.to_regclass('public.workspaces')" in MIGRATION
