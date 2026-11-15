from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text('<html><div id="root"></div></html>', encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log(1);", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("SECRET", encoding="utf-8")
    return web


@pytest.fixture
def client(static_dir: Path) -> TestClient:
    return TestClient(create_app(Settings(api_prefix="/api", static_dir=static_dir)))


def test_routes_under_prefix(client):
    assert client.get("/api/products/search?q=x").status_code == 401


def test_spa_fallback_deep_link(client):
    r = client.get("/week/2027-W01")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="root"' in r.text


def test_static_asset_served(client):
    r = client.get("/assets/app.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]


def test_path_traversal_blocked(client):
    r = client.get("/..%2f..%2fpyproject.toml")
    assert "SECRET" not in r.text
    assert r.status_code == 404 or 'id="root"' in r.text


def test_api_unknown_path_is_json_404(client):
    r = client.get("/api/unknown")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_post_unknown_path_not_fallback(client):
    r = client.post("/week")
    assert r.status_code in (404, 405)
    assert not r.headers.get("content-type", "").startswith("text/html")


def test_defaults_unchanged():
    client = TestClient(create_app(Settings()))
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/products/search?q=x").status_code == 401
    assert client.get("/week").status_code == 404
