from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RECORDS_SQL = (
    ROOT / "docs" / "supabase" / "people_registry_records.sql"
).read_text(encoding="utf-8")
INVITES_SQL = (
    ROOT / "docs" / "supabase" / "people_registry_invites.sql"
).read_text(encoding="utf-8")


class PeopleRegistryMigrationContractTests(unittest.TestCase):
    def test_records_baseline_uses_uuid_edit_token(self) -> None:
        self.assertIn(
            "edit_token uuid not null default gen_random_uuid()",
            RECORDS_SQL.lower(),
        )
        self.assertNotIn("edit_token text", RECORDS_SQL.lower())

    def test_invites_migration_rejects_edit_token_schema_drift(self) -> None:
        normalized = INVITES_SQL.lower()
        self.assertIn("edit_token uuid", normalized)
        self.assertIn("must be uuid", normalized)
        self.assertNotIn("edit_token text", normalized)

    def test_records_and_invites_are_backend_only(self) -> None:
        normalized = INVITES_SQL.lower()
        for table_name in (
            "people_registry_records",
            "people_registry_invites",
        ):
            self.assertIn(
                f"alter table public.{table_name} enable row level security",
                normalized,
            )
            self.assertIn(
                f"revoke all privileges on table public.{table_name}",
                normalized,
            )


if __name__ == "__main__":
    unittest.main()
