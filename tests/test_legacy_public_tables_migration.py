from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260808185409_harden_legacy_public_tables.sql"
).read_text(encoding="utf-8").lower()

SERVER_ONLY_TABLES = (
    "submissions_revisions",
    "ai_usage_log",
    "release_intake_drafts",
    "workspace_airtable_mapping",
    "workspace_field_overrides",
    "workspace_branding",
    "workspace_plan_overrides",
    "workspace_workflow_settings",
)


def test_legacy_tables_are_declared_server_only() -> None:
    for table_name in SERVER_ONLY_TABLES:
        assert f"'{table_name}'" in MIGRATION

    assert "enable row level security" in MIGRATION
    assert "from public, anon, authenticated" in MIGRATION
    assert "grant select, insert, update, delete" in MIGRATION
    assert "to service_role" in MIGRATION


def test_migration_is_safe_for_incomplete_local_baselines() -> None:
    assert "pg_catalog.to_regclass" in MIGRATION
    assert "is not null" in MIGRATION


def test_legacy_trigger_functions_have_fixed_search_paths() -> None:
    assert "alter function public.set_updated_at() set search_path" in MIGRATION
    assert (
        "alter function public.touch_setup_ai_config_drafts_updated_at() set search_path"
        in MIGRATION
    )
