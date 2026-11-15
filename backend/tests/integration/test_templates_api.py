"""GET /meal-templates/{id}: items, per-item nutrients and totals."""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.api.auth import CurrentUser, get_current_user
from app.api.routes_products import get_connection
from app.db import catalogue
from app.db.engine import make_engine
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.domain.values import Nutrient, NutrientValue, Source
from app.main import create_app

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def engine():
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"test_tpl_{uuid.uuid4().hex[:12]}"
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
def conn(engine):
    with engine.connect() as c:
        tx = c.begin()
        try:
            yield c
        finally:
            tx.rollback()


def _app(conn, authed=True):
    app = create_app()

    def _override_conn():
        yield conn

    app.dependency_overrides[get_connection] = _override_conn
    if authed:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(oid="owner")
    return TestClient(app)


@pytest.fixture
def client(conn):
    return _app(conn)


def _product(conn, name, name_en):
    pid = catalogue.insert_product(
        conn, owner="catalogue", kind="product", name=name, brand=None,
        basis_unit=Unit.G, state="as_sold",
    )
    conn.execute(sa.text("UPDATE product SET name_en = :e WHERE id = :i"), {"e": name_en, "i": pid})
    return pid


def _publish(conn, products):
    release = catalogue.create_release(conn, "r")
    for pid, values in products.items():
        out = catalogue.add_source_revision(conn, pid, Source.CIQUAL, "x", {}, values)
        catalogue.set_product_state(
            conn, release, pid, ProductState(Status.ACTIVE), out.live_revision_id
        )
    catalogue.activate_release(conn, release)


def _template(conn, items):
    tid = conn.execute(
        sa.text(
            "INSERT INTO meal_template (name, name_en, cuisine, chain, estimate, source_url) "
            "VALUES (:n, 'Chicken and rice', 'fr', NULL, true, 'http://x') RETURNING id"
        ),
        {"n": f"poulet riz {uuid.uuid4().hex[:6]}"},
    ).scalar_one()
    for pos, (pid, grams) in enumerate(items):
        conn.execute(
            sa.text(
                "INSERT INTO meal_template_item (template_id, product_id, grams, position) "
                "VALUES (:t, :p, :g, :pos)"
            ),
            {"t": tid, "p": pid, "g": grams, "pos": pos},
        )
    return tid


def _pt(v):
    return NutrientValue.point(Decimal(v), Source.CIQUAL)


def test_template_returns_items_and_totals(client, conn):
    rice = _product(conn, "Riz", "Rice")
    chicken = _product(conn, "Poulet", "Chicken")
    _publish(conn, {
        rice: {Nutrient.ENERGY_KCAL: _pt(130), Nutrient.PROTEIN: _pt(3)},
        chicken: {Nutrient.ENERGY_KCAL: _pt(165), Nutrient.PROTEIN: _pt(31)},
    })
    tid = _template(conn, [(chicken, 150), (rice, 200)])

    r = client.get(f"/meal-templates/{tid}")
    assert r.status_code == 200
    body = r.json()
    assert body["name_en"] == "Chicken and rice"
    assert body["cuisine"] == "fr" and body["chain"] is None and body["estimate"] is True
    assert body["source_url"] == "http://x"
    assert [i["name_en"] for i in body["items"]] == ["Chicken", "Rice"]
    first = body["items"][0]
    assert first["product_id"] == chicken and first["grams"] == "150"
    assert first["nutrients"]["energy_kcal"] == {"known": True, "lower": "247.5", "upper": "247.5"}
    assert body["totals"]["energy_kcal"] == {"known": True, "lower": "507.5", "upper": "507.5"}
    assert body["totals"]["protein"]["lower"] == "52.5"


def test_template_total_unknown_when_ingredient_unknown(client, conn):
    rice = _product(conn, "Riz", "Rice")
    chicken = _product(conn, "Poulet", "Chicken")
    _publish(conn, {
        rice: {Nutrient.ENERGY_KCAL: _pt(130), Nutrient.SALT: NutrientValue.unknown(Source.CIQUAL)},
        chicken: {Nutrient.ENERGY_KCAL: _pt(165), Nutrient.SALT: _pt("0.2")},
    })
    tid = _template(conn, [(chicken, 100), (rice, 100)])

    body = client.get(f"/meal-templates/{tid}").json()
    assert body["totals"]["salt"] == {"known": False, "lower": None, "upper": None}
    assert body["totals"]["energy_kcal"]["lower"] == "295"
    # a nutrient neither product records is unknown, never 0
    assert body["totals"]["caffeine"]["known"] is False


def test_template_404(client):
    assert client.get("/meal-templates/999999").status_code == 404


def test_template_requires_auth(conn):
    c = _app(conn, authed=False)
    assert c.get("/meal-templates/1").status_code == 401


def test_template_name_is_unique(conn):
    insert = sa.text(
        "INSERT INTO meal_template (name, name_en, cuisine, estimate) "
        "VALUES ('Unique template name', 'Unique template name', 'fr', true)"
    )
    conn.execute(insert)
    with pytest.raises(sa.exc.IntegrityError):
        conn.execute(insert)


def test_meal_template_item_product_id_is_indexed(conn):
    indexes = sa.inspect(conn).get_indexes("meal_template_item")
    assert "ix_meal_template_item_product_id" in {index["name"] for index in indexes}
