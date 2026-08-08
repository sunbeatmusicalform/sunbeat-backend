from app.main import _trusted_hosts


def test_trusted_hosts_adds_only_valid_explicit_hosts() -> None:
    hosts = _trusted_hosts(
        "sunbeat-market-readiness-qa.fly.dev, QA.EXAMPLE.COM, bad host, https://bad.example"
    )

    assert "sunbeat-market-readiness-qa.fly.dev" in hosts
    assert "qa.example.com" in hosts
    assert "bad host" not in hosts
    assert "https://bad.example" not in hosts


def test_trusted_hosts_deduplicates_existing_hosts() -> None:
    hosts = _trusted_hosts("sunbeat.pro,sunbeat.pro")

    assert hosts.count("sunbeat.pro") == 1
