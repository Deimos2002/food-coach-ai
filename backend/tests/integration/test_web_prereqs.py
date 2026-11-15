"""Backend prerequisites for the local web app (Task 0): CORS, saved-meal list, and
recent items, against a real Postgres. Follows the fixture pattern of
``tests/integration/test_body_saved.py``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.api import routes_meals, routes_saved
from app.api.auth import CurrentUser, get_current_user
from app.config import Settings
from app.db import catalogue
from app.db.engine import make_engine
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.main import create_app

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]

TRUNCATE_SQL = (
    "TRUNCATE saved_meal_item, saved_meal, meal_item, meal, day, idempotency_key, "
    "product_state, nutrient_override, nutrient_value, profile_revision, "
    "source_record, product, active_release, catalogue_release, user_account "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture(scope="module")
def engine():
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"test_web_prereqs_{uuid.uuid4().hex[:12]}"
    admin = sa.create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = make_url(base_url).set(database=name).render_as_string(hide_password=False)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    eng = make_engine(url)
    try:
        yield eng
    finally:
        eng.dispose()
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def client(engine, settings):
    app = create_app(settings)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(oid="test-owner")
    app.dependency_overrides[routes_saved.get_db_engine] = lambda: engine
    app.dependency_overrides[routes_meals.get_db_engine] = lambda: engine
    try:
        yield TestClient(app)
    finally:
        with engine.begin() as c:
            c.execute(sa.text(TRUNCATE_SQL))


@pytest.fixture
def anon_client(engine, settings):
    """A client with no auth override, for 401 / CORS checks."""
    app = create_app(settings)
    app.dependency_overrides[routes_saved.get_db_engine] = lambda: engine
    app.dependency_overrides[routes_meals.get_db_engine] = lambda: engine
    try:
        yield TestClient(app)
    finally:
        with engine.begin() as c:
            c.execute(sa.text(TRUNCATE_SQL))


def _meal(client, day: str = "2026-10-12", meal_type: str = "lunch") -> int:
    r = client.post("/meals", json={"day": day, "meal_type": meal_type})
    assert r.status_code == 201, r.text
    return r.json()["meal_id"]


def _add(client, meal_id: int, product_id: int, amount="100", unit="g", key=None, **extra):
    body = {
        "product_id": product_id,
        "amount": amount,
        "unit": unit,
        "quantity_source": "weighed",
        **extra,
    }
    return client.post(
        f"/meals/{meal_id}/items",
        json=body,
        headers={"Idempotency-Key": key or uuid.uuid4().hex},
    )


# --- CORS -----------------------------------------------------------------------------------


def test_cors_preflight_allows_dev_origin(anon_client):
    r = anon_client.options(
        "/saved-meals",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,idempotency-key",
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_rejects_other_origin(anon_client):
    r = anon_client.options(
        "/saved-meals",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,idempotency-key",
        },
    )
    assert "access-control-allow-origin" not in r.headers


# --- Saved meals list -------------------------------------------------------------------------


def _make_product(engine, name: str, basis: Unit = Unit.G, brand: str | None = None) -> int:
    from app.domain.values import Source

    with engine.begin() as c:
        pid = catalogue.insert_product(
            c, owner="catalogue", kind="product", name=name, brand=brand,
            basis_unit=basis, state="as_sold",
        )
        rev = catalogue.add_source_revision(c, pid, Source.CIQUAL, name, {}, {})
        release = catalogue.create_release(c, f"release {name}")
        prev = c.execute(sa.text("SELECT release_id FROM active_release")).scalar_one_or_none()
        if prev is not None:
            c.execute(
                sa.text(
                    "INSERT INTO product_state (release_id, product_id, status, merged_into, "
                    "successors, active_revision_id) SELECT :new, product_id, status, "
                    "merged_into, successors, active_revision_id FROM product_state "
                    "WHERE release_id = :old ON CONFLICT DO NOTHING"
                ),
                {"new": release, "old": prev},
            )
        catalogue.set_product_state(
            c, release, pid, ProductState(Status.ACTIVE), rev.live_revision_id
        )
        catalogue.activate_release(c, release)
    return pid


def test_list_saved_meals(client, anon_client, engine):
    pid_b = _make_product(engine, "Banane")
    pid_a = _make_product(engine, "Avocat")

    r1 = client.post(
        "/saved-meals",
        json={"name": "Zebre meal", "items": [{"product_id": pid_b, "amount": "100.5", "unit": "g"}]},
    )
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        "/saved-meals",
        json={"name": "Alpha meal", "items": [{"product_id": pid_a, "amount": "50", "unit": "g"}]},
    )
    assert r2.status_code == 201, r2.text

    r = client.get("/saved-meals")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [m["name"] for m in body] == ["Alpha meal", "Zebre meal"]
    alpha = body[0]
    assert alpha["items"][0]["product_id"] == pid_a
    assert alpha["items"][0]["amount"] == "50"
    assert alpha["items"][0]["unit"] == "g"
    assert isinstance(alpha["items"][0]["amount"], str)

    r_noauth = anon_client.get("/saved-meals")
    assert r_noauth.status_code == 401
    assert r_noauth.json() == {"detail": "unauthorized"}


# --- Recent items -----------------------------------------------------------------------------


def test_recent_items_distinct_newest_first(client, engine):
    pid_a = _make_product(engine, "Pomme", brand="Brand")
    pid_b = _make_product(engine, "Poire", brand="Brand")

    meal_id = _meal(client)
    assert _add(client, meal_id, pid_a, amount="100").status_code == 201
    assert _add(client, meal_id, pid_b, amount="200").status_code == 201
    r3 = _add(client, meal_id, pid_a, amount="150")
    assert r3.status_code == 201, r3.text

    r = client.get("/recent-items")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [item["product_id"] for item in body] == [pid_a, pid_b]
    assert body[0]["last_amount"] == "150"
    assert body[0]["last_unit"] == "g"
    assert body[0]["brand"] == "Brand"
    assert body[0]["basis_unit"] == "g"
    assert "last_logged_at" in body[0]


def test_recent_items_limit_bounds(client):
    assert client.get("/recent-items", params={"limit": 0}).status_code == 422
    assert client.get("/recent-items", params={"limit": 51}).status_code == 422
    assert client.get("/recent-items", params={"limit": 1}).status_code == 200
