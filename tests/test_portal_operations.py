import asyncio

from app.modules import portal_operations


def test_test_projects_are_not_exposed():
    assert portal_operations._looks_like_test("teste")
    assert portal_operations._looks_like_test("[TESTE] Integração")
    assert not portal_operations._looks_like_test("Disparada ao vivo em Búzios")


def test_portal_data_uses_only_live_sources(monkeypatch):
    monkeypatch.setattr(portal_operations, "_submission_rows", lambda _slug: [])
    monkeypatch.setattr(portal_operations, "_airtable_data", lambda: {
        "projects": [{"id": "rec-real", "title": "Projeto real"}],
        "stages": [],
        "demands": [],
    })
    monkeypatch.setattr(portal_operations, "_invite_items", lambda _slug: [])

    result = asyncio.run(portal_operations.get_portal_data("atabaque", None))

    assert result["source"] == "airtable"
    assert result["projects"] == [{"id": "rec-real", "title": "Projeto real"}]
    assert result["invites"] == []


def test_portal_data_falls_back_to_real_submissions(monkeypatch):
    monkeypatch.setattr(portal_operations, "_submission_rows", lambda _slug: [
        {"id": "real", "release_title": "Projeto confirmado", "airtable_sync_status": "pending"},
        {"id": "fake", "release_title": "teste", "airtable_sync_status": "synced"},
    ])
    monkeypatch.setattr(portal_operations, "_airtable_data", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(portal_operations, "_invite_items", lambda _slug: [])

    result = asyncio.run(portal_operations.get_portal_data("atabaque", None))

    assert result["source"] == "supabase"
    assert [item["title"] for item in result["projects"]] == ["Projeto confirmado"]
    assert result["source_error"] == "Airtable temporariamente indisponível"
