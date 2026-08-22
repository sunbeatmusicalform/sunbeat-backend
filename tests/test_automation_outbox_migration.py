from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "supabase" / "migrations" / "20260822042144_activepieces_automation_outbox.sql"


def test_outbox_migration_is_private_idempotent_and_claims_atomically():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "idempotency_key text not null unique" in sql
    assert "enable row level security" in sql
    assert "revoke all on table public.automation_outbox from anon, authenticated" in sql
    assert "for update skip locked" in sql
    assert "status = 'sending' and locked_at < now() - interval '15 minutes'" in sql
    assert "workspace slug is required" in sql
    assert "workspace_slug = lower(trim(p_workspace_slug))" in sql
    assert "security definer" in sql
    assert "revoke all on function public.claim_automation_outbox" in sql
