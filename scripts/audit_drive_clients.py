from __future__ import annotations

import json

from app.services.google_drive import audit_airtable_client_drive_folders


def main() -> None:
    result = audit_airtable_client_drive_folders()
    safe_clients = [
        {
            "record_id": client["record_id"],
            "client_name": client["client_name"],
            "status": client["status"],
            "artist_folder_source": client["artist_folder_source"],
            "has_artist_folder_id": client["has_artist_folder_id"],
            "has_drive_link_folder_id": client["has_drive_link_folder_id"],
            "has_projects_folder_id": client["has_projects_folder_id"],
        }
        for client in result["clients"]
    ]
    print(
        json.dumps(
            {
                "ok": result["ok"],
                "mode": result["mode"],
                "total": result["total"],
                "status_counts": result["status_counts"],
                "clients": safe_clients,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
