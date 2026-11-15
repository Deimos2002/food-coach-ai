import asyncio
import os
import uuid
from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest
import respx
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.db import catalogue
from app.db.engine import make_engine
from app.db.tables import portion, product, product_state
from app.domain.quantity import Unit
from app.domain.values import Nutrient, Range, Source
from app.importers.off import (
    NeedsBasis,
    detect_basis,
    fetch_off_product,
    import_off_product,
    search_off,
)

BACKEND = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_import_red_bull_peach_fixture(conn):
    pid = import_off_product(
        conn,
        {
            "code": "123",
            "product_name": "Red Bull Peach",
            "quantity": "250 ml",
            "product_quantity": 250,
            "nutriments": {"sugars_100g": 11},
        },
        None,
        1,
    )
    row = conn.execute(sa.select(product).where(product.c.id == pid)).mappings().one()
    assert row["basis_unit"] == "ml"
    assert row["owner"] == "user"
    assert row["data_generation"] == 1
    _, values = catalogue.active_values(conn, pid)
    assert values[Nutrient.SUGARS].range == Range.point(D(11))
    assert values[Nutrient.SUGARS].source == Source.OFF_CONTRIBUTOR
    assert not values[Nutrient.CAFFEINE].known
    state = (
        conn.execute(sa.select(product_state).where(product_state.c.product_id == pid))
        .mappings()
        .one()
    )
    assert state["release_id"] is None
    assert state["status"] == "active"
    package = conn.execute(sa.select(portion).where(portion.c.product_id == pid)).mappings().one()
    assert package["amount"] == D(250)
    assert package["kind"] == "package"


@pytest.mark.integration
@pytest.mark.parametrize("nutriments", [{}, None])
def test_review_focus_no_nutriments_all_unknown(conn, nutriments):
    pid = import_off_product(
        conn, {"code": "456", "quantity": "100 g", "nutriments": nutriments}, None, 1
    )
    _, values = catalogue.active_values(conn, pid)
    assert set(values) == set(Nutrient)
    assert all(not value.known and value.range is None for value in values.values())


def test_unknown_basis_raises_needs_basis():
    with pytest.raises(NeedsBasis):
        import_off_product(None, {"code": "789"}, None, 1)


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        ("33 cl", Unit.ML),
        ("1 kg", Unit.G),
        ("6 x 25 cl", Unit.ML),
        ("250ML", Unit.ML),
        ("1 l", Unit.ML),
        (None, None),
        ("6 pieces", None),
    ],
)
def test_detect_basis_cases(quantity, expected):
    assert detect_basis(quantity) == expected


@pytest.mark.integration
def test_override_and_known_zero(conn):
    pid = import_off_product(conn, {"code": "0", "nutriments": {"sugars_100g": 0}}, Unit.G, 2)
    _, values = catalogue.active_values(conn, pid)
    assert values[Nutrient.SUGARS].range == Range.point(D(0))


@respx.mock
def test_fetch_product():
    route = respx.get("https://world.openfoodfacts.org/api/v2/product/123.json").respond(
        200, json={"status": 1, "product": {"code": "123"}}
    )

    async def run():
        async with httpx.AsyncClient() as client:
            return await fetch_off_product(client, "123")

    assert asyncio.run(run()) == {"code": "123"}
    assert route.calls.last.request.headers["User-Agent"] == "FoodCoach/0.1 (personal)"


@respx.mock
@pytest.mark.parametrize("status,body", [(404, {}), (200, {"status": 0})])
def test_fetch_missing_product(status, body):
    respx.get("https://world.openfoodfacts.org/api/v2/product/123.json").respond(status, json=body)

    async def run():
        async with httpx.AsyncClient() as client:
            return await fetch_off_product(client, "123")

    assert asyncio.run(run()) is None


@respx.mock
def test_search_france():
    route = respx.get(
        "https://world.openfoodfacts.org/cgi/search.pl",
        params={
            "search_terms": "peche",
            "json": "1",
            "page_size": "3",
            "tagtype_0": "countries",
            "tag_contains_0": "contains",
            "tag_0": "france",
        },
    ).respond(200, json={"products": [{"code": "123"}]})

    async def run():
        async with httpx.AsyncClient() as client:
            return await search_off(client, "peche", 3)

    assert asyncio.run(run()) == [{"code": "123"}]
    assert route.called


@pytest.mark.integration
@respx.mock
def test_fetch_product_preserves_decimal_precision(conn):
    body = (
        '{"status": 1, "product": {"code": "123", "quantity": "100 g", '
        '"nutriments": {"sugars_100g": 11.123456789012345678}}}'
    )
    respx.get("https://world.openfoodfacts.org/api/v2/product/123.json").respond(
        200, content=body, headers={"Content-Type": "application/json"}
    )

    async def run():
        async with httpx.AsyncClient() as client:
            return await fetch_off_product(client, "123")

    product_json = asyncio.run(run())
    pid = import_off_product(conn, product_json, None, 1)
    _, values = catalogue.active_values(conn, pid)
    assert values[Nutrient.SUGARS].range == Range.point(D("11.123456789012345678"))


@pytest.fixture(scope="module")
def engine():
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"test_ciqual_{uuid.uuid4().hex[:12]}"
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
