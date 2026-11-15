import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_ok():
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.integration
def test_deep_health_ok():
    import os

    from app.db.engine import get_engine, make_engine

    app = create_app()
    eng = make_engine(os.environ["TEST_DATABASE_URL"])
    app.dependency_overrides[get_engine] = lambda: eng
    try:
        response = TestClient(app).get("/health?deep=1")
    finally:
        eng.dispose()
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_deep_health_db_down():
    from app.db.engine import get_engine

    def _down():
        raise RuntimeError("db down")

    app = create_app()
    app.dependency_overrides[get_engine] = _down
    response = TestClient(app).get("/health?deep=1")
    assert response.status_code == 503
