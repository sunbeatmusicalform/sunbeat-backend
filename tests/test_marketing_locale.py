from fastapi.testclient import TestClient

from app.main import _inject_marketing_locale, app


HTML = """<!doctype html><html lang="en"><head>
<title>Old</title>
<meta name="description" content="Old" />
<meta property="og:title" content="Old" />
<meta property="og:description" content="Old" />
</head><body></body></html>"""


def test_injects_brazilian_portuguese_metadata() -> None:
    result = _inject_marketing_locale(HTML, "www.sunbeat.com.br")

    assert '<html lang="pt-BR">' in result
    assert "Intake inteligente para operações criativas" in result
    assert '<link rel="canonical" href="https://sunbeat.com.br/" />' in result
    assert 'hreflang="en" href="https://sunbeat.pro/"' in result


def test_injects_global_english_metadata() -> None:
    result = _inject_marketing_locale(HTML, "sunbeat.pro")

    assert '<html lang="en">' in result
    assert "Intelligent intake for creative operations" in result
    assert '<link rel="canonical" href="https://sunbeat.pro/" />' in result
    assert 'hreflang="pt-BR" href="https://sunbeat.com.br/"' in result


def test_marketing_html_is_never_served_from_stale_browser_cache() -> None:
    response = TestClient(app).get("/", headers={"host": "sunbeat.pro"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"


def test_academy_article_has_localized_social_and_structured_metadata() -> None:
    result = _inject_marketing_locale(
        HTML.replace(
            "</head>",
            '<meta property="og:type" content="website" /><meta property="og:image" content="/image.png" /></head>',
        ),
        "sunbeat.com.br",
        "/academy/music-release-intake-checklist",
    )

    assert "Checklist de intake para lançamentos musicais" in result
    assert 'content="article"' in result
    assert 'content="https://sunbeat.com.br/brand/og-image.png"' in result
    assert '"@type":"BlogPosting"' in result
    assert 'href="https://sunbeat.pro/academy/music-release-intake-checklist"' in result


def test_robots_and_sitemap_are_valid_discovery_documents() -> None:
    client = TestClient(app)

    robots = client.get("/robots.txt", headers={"host": "sunbeat.pro"})
    sitemap = client.get("/sitemap.xml", headers={"host": "sunbeat.com.br"})
    feed = client.get("/feed.xml", headers={"host": "sunbeat.pro"})

    assert robots.status_code == 200
    assert robots.text.startswith("User-agent: *")
    assert "https://sunbeat.pro/sitemap.xml" in robots.text
    assert sitemap.status_code == 200
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert "https://sunbeat.com.br/academy" in sitemap.text
    assert 'hreflang="en"' in sitemap.text
    assert feed.headers["content-type"].startswith("application/rss+xml")
    assert "The music release intake checklist" in feed.text


def test_operational_spa_routes_are_noindex() -> None:
    response = TestClient(app).get("/portal/example", headers={"host": "sunbeat.pro"})

    assert response.status_code == 200
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
