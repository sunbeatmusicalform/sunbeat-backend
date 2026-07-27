from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.modules.people_registry import router
from app.schemas.people_registry import (
    PeopleRegistryInviteListResponsePayload,
    PeopleRegistryInvitePayload,
    PeopleRegistryInviteResponsePayload,
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _invite() -> PeopleRegistryInvitePayload:
    return PeopleRegistryInvitePayload(
        token="token-123",
        status="pending",
        workspace_slug="atabaque",
        profile="atabaque_people_v1",
        airtable_clearance_part_id="",
        invite_url="https://sunbeat.pro/people/atabaque?invite=token-123",
        context={},
    )


class PeopleRegistryInviteAuthTests(unittest.TestCase):
    def test_internal_invite_routes_require_admin_token(self) -> None:
        with patch.object(settings, "INTERNAL_ADMIN_TOKEN", "secret"):
            client = _client()

            self.assertEqual(client.get("/people-registry/invites?workspace_slug=atabaque").status_code, 401)
            self.assertEqual(
                client.post(
                    "/people-registry/invites",
                    json={"workspace_slug": "atabaque", "profile": "atabaque_people_v1"},
                ).status_code,
                401,
            )
            self.assertEqual(
                client.post("/people-registry/invites/token-123/email", json={}).status_code,
                401,
            )

    def test_internal_invite_list_accepts_valid_admin_token(self) -> None:
        with (
            patch.object(settings, "INTERNAL_ADMIN_TOKEN", "secret"),
            patch(
                "app.modules.people_registry.list_people_registry_invites_response",
                return_value=PeopleRegistryInviteListResponsePayload(
                    ok=True,
                    items=[_invite()],
                    total=1,
                ),
            ),
        ):
            response = _client().get(
                "/people-registry/invites?workspace_slug=atabaque",
                headers={"X-Admin-Token": "secret"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)

    def test_internal_invite_list_rejects_supabase_bearer_token(self) -> None:
        with (
            patch.object(settings, "INTERNAL_ADMIN_TOKEN", "secret"),
            patch(
                "app.core.admin_auth._supabase_user_token_is_valid",
                return_value=True,
            ),
        ):
            response = _client().get(
                "/people-registry/invites?workspace_slug=atabaque",
                headers={"Authorization": "Bearer user-session"},
            )

        self.assertEqual(response.status_code, 401)

    def test_public_invite_read_stays_public(self) -> None:
        with (
            patch.object(settings, "INTERNAL_ADMIN_TOKEN", "secret"),
            patch(
                "app.modules.people_registry.get_people_registry_invite_response",
                return_value=PeopleRegistryInviteResponsePayload(
                    ok=True,
                    status="pending",
                    invite=_invite(),
                ),
            ),
        ):
            response = _client().get("/people-registry/invites/token-123")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["invite"]["token"], "token-123")


if __name__ == "__main__":
    unittest.main()
