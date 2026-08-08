import asyncio

from app.modules import portal_operations


def test_test_projects_are_not_exposed():
    assert portal_operations._looks_like_test("teste")
    assert portal_operations._looks_like_test("[TESTE] Integração")
    assert not portal_operations._looks_like_test("Disparada ao vivo em Búzios")


def test_url_normalization_accepts_only_web_links():
    assert portal_operations._urls([
        {"url": "https://drive.google.com/file/123"},
        "https://cdn.example.com/capa.png",
        "javascript:alert(1)",
    ]) == ["https://drive.google.com/file/123", "https://cdn.example.com/capa.png"]


def test_airtable_demand_resolves_project_through_calendar(monkeypatch):
    def records(table, _fields, _limit=100):
        if table == portal_operations.PROJECTS_TABLE:
            return [{"id": "recProject", "fields": {
                "Nome do Projeto": "Projeto real", "Link da Capa": "https://cdn.example.com/capa.png",
            }}]
        if table == portal_operations.TRACKS_TABLE:
            return [{"id": "recTrack", "fields": {
                "Projeto": ["recProject"], "Link do Áudio (WAV)": "https://cdn.example.com/audio.wav",
            }}]
        if table == portal_operations.DEMANDS_TABLE:
            return [{"id": "recDemand", "fields": {
                "Ticket da Demanda": "DEM-1", "Produto": ["recCalendar"],
                "Produto (from Produto)": ["Projeto real"],
            }}]
        return []

    monkeypatch.setattr(portal_operations, "_records", records)
    monkeypatch.setattr(portal_operations, "_records_by_ids", lambda *_args: [{
        "id": "recCalendar", "fields": {"Origem Projeto ID": "recProject"},
    }])

    result = portal_operations._airtable_data()

    assert result["demands"][0]["project_id"] == "recProject"
    assert [item["label"] for item in result["demands"][0]["file_links"]] == ["Capa", "Áudio 1"]


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


def test_portal_data_links_project_drive_folder_to_demand(monkeypatch):
    monkeypatch.setattr(portal_operations, "_submission_rows", lambda _slug: [{
        "id": "submission-real",
        "release_title": "Projeto confirmado",
        "airtable_project_id": "rec-project",
        "google_drive_folder_id": "drive-folder",
    }])
    monkeypatch.setattr(portal_operations, "_airtable_data", lambda: {
        "projects": [{"id": "rec-project", "title": "Projeto confirmado"}],
        "stages": [],
        "demands": [{
            "id": "rec-demand",
            "project_id": "rec-project",
            "file_links": [{"label": "Capa", "url": "https://cdn.example.com/capa.png"}],
        }],
    })
    monkeypatch.setattr(portal_operations, "_invite_items", lambda _slug: [])

    result = asyncio.run(portal_operations.get_portal_data("atabaque", None))

    assert result["demands"][0]["file_links"] == [
        {"label": "Pasta do projeto", "url": "https://drive.google.com/drive/folders/drive-folder"},
        {"label": "Capa", "url": "https://cdn.example.com/capa.png"},
    ]
    assert result["drive_folders"][0]["project_id"] == "rec-project"


def test_other_tenant_never_reads_atabaque_airtable(monkeypatch):
    monkeypatch.setattr(portal_operations, "_submission_rows", lambda _slug: [])
    monkeypatch.setattr(
        portal_operations,
        "_airtable_data",
        lambda: (_ for _ in ()).throw(AssertionError("Atabaque Airtable must not be read")),
    )
    monkeypatch.setattr(portal_operations, "_invite_items", lambda _slug: [])
    monkeypatch.setattr(
        portal_operations,
        "get_workflow_settings",
        lambda *_args: {"airtable_sync_enabled": True},
    )

    result = asyncio.run(portal_operations.get_portal_data("sunbeat-qa-isolated", None))

    assert result["source"] == "supabase"
    assert result["projects"] == []
    assert result["integrations"]["airtable"] == {
        "configured": False,
        "status": "disabled",
    }
