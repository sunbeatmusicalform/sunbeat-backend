from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_http_checks_use_an_allowed_host_header() -> None:
    config = tomllib.loads((ROOT / "fly.toml").read_text(encoding="utf-8"))

    checks = config["http_service"]["checks"]

    assert {check["path"] for check in checks} == {"/health", "/readiness"}
    assert all(check["headers"]["Host"] == "sunbeat-backend.fly.dev" for check in checks)
