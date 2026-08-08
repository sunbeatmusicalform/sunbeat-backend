from fastapi.testclient import TestClient

from app.main import app


def test_portuguese_legal_pages_are_public_and_localized() -> None:
    client = TestClient(app)

    terms = client.get("/terms", headers={"Host": "sunbeat.com.br"})
    privacy = client.get("/privacy", headers={"Host": "sunbeat.com.br"})

    assert terms.status_code == 200
    assert '<html lang="pt-BR"' in terms.text
    assert "<title>Termos de Uso | Sunbeat</title>" in terms.text
    assert 'rel="canonical" href="https://sunbeat.com.br/terms"' in terms.text
    assert "x-robots-tag" not in terms.headers

    assert privacy.status_code == 200
    assert "<title>Política de Privacidade | Sunbeat</title>" in privacy.text
    assert 'rel="canonical" href="https://sunbeat.com.br/privacy"' in privacy.text
    assert "x-robots-tag" not in privacy.headers


def test_english_legal_pages_are_public_and_localized() -> None:
    client = TestClient(app)

    terms = client.get("/terms", headers={"Host": "sunbeat.pro"})
    privacy = client.get("/privacy", headers={"Host": "sunbeat.pro"})

    assert terms.status_code == 200
    assert '<html lang="en"' in terms.text
    assert "<title>Terms of Use | Sunbeat</title>" in terms.text
    assert 'rel="canonical" href="https://sunbeat.pro/terms"' in terms.text

    assert privacy.status_code == 200
    assert "<title>Privacy Policy | Sunbeat</title>" in privacy.text
    assert 'rel="canonical" href="https://sunbeat.pro/privacy"' in privacy.text


def test_portuguese_legal_aliases_remain_available() -> None:
    client = TestClient(app)

    assert client.get("/termos", headers={"Host": "sunbeat.com.br"}).status_code == 200
    assert client.get("/privacidade", headers={"Host": "sunbeat.com.br"}).status_code == 200
