"""Testes da sessão do portal e do branding self-service."""
from __future__ import annotations

import hashlib
import os
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("INTERNAL_ADMIN_TOKEN", "admin-secret")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("PORTAL_PASS_SHA256", hashlib.sha256(b"senha-teste").hexdigest())

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.modules.portal_branding import router as branding_router
from app.modules.portal_session import issue_portal_token, portal_token_is_valid, router as session_router

app = FastAPI()
app.include_router(session_router)
app.include_router(branding_router)
client = TestClient(app)

PASS = "senha-teste"
# alinha o settings compartilhado entre módulos de teste com a senha deste arquivo
settings.PORTAL_PASS_SHA256 = hashlib.sha256(PASS.encode()).hexdigest()


class PortalSessionTests(unittest.TestCase):
    def test_token_roundtrip(self):
        token = issue_portal_token("atabaque")
        self.assertTrue(portal_token_is_valid(token, "atabaque"))
        self.assertFalse(portal_token_is_valid(token, "outro-workspace"))
        self.assertFalse(portal_token_is_valid(token + "x", "atabaque"))

    def test_token_expirado(self):
        token = issue_portal_token("atabaque", expires_at=int(time.time()) - 10)
        self.assertFalse(portal_token_is_valid(token, "atabaque"))

    def test_create_session_ok(self):
        res = client.post("/workspaces/atabaque/portal-session", json={"password": PASS})
        self.assertEqual(res.status_code, 200)
        self.assertIn("token", res.json())

    def test_create_session_senha_errada(self):
        res = client.post("/workspaces/atabaque/portal-session", json={"password": "errada"})
        self.assertEqual(res.status_code, 401)


class BrandingPatchTests(unittest.TestCase):
    def _supabase_mock(self):
        table = MagicMock()
        sup = MagicMock()
        sup.table.return_value = table
        return sup, table

    def test_patch_sem_token(self):
        res = client.patch("/workspaces/atabaque/branding", json={"workspace_name": "X"})
        self.assertEqual(res.status_code, 401)

    def test_patch_com_portal_token(self):
        sup, table = self._supabase_mock()
        token = issue_portal_token("atabaque")
        with patch("app.modules.portal_branding.supabase", sup):
            res = client.patch(
                "/workspaces/atabaque/branding",
                json={"workspace_name": "Atabaque Novo", "primary_color": "#329fd7"},
                headers={"X-Portal-Token": token},
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(sorted(res.json()["updated"]), ["primary_color", "workspace_name"])
        table.update.assert_called_once()

    def test_patch_com_admin_token(self):
        sup, table = self._supabase_mock()
        with patch("app.modules.portal_branding.supabase", sup):
            res = client.patch(
                "/workspaces/atabaque/branding",
                json={"slogan": "Novo slogan"},
                headers={"X-Admin-Token": settings.INTERNAL_ADMIN_TOKEN or "admin-secret"},
            )
        self.assertEqual(res.status_code, 200)

    def test_cor_invalida_rejeitada(self):
        token = issue_portal_token("atabaque")
        res = client.patch(
            "/workspaces/atabaque/branding",
            json={"primary_color": "azul"},
            headers={"X-Portal-Token": token},
        )
        self.assertEqual(res.status_code, 422)

    def test_campo_desconhecido_rejeitado(self):
        token = issue_portal_token("atabaque")
        res = client.patch(
            "/workspaces/atabaque/branding",
            json={"campo_proibido": "x"},
            headers={"X-Portal-Token": token},
        )
        self.assertEqual(res.status_code, 422)


if __name__ == "__main__":
    unittest.main()
