"""Curated food data importer against a real Postgres (food data pack, Task 3)."""

from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.db import catalogue
from app.db.engine import make_engine
from app.domain.values import Nutrient, Source
from app.importers.ciqual import import_ciqual
from app.importers.ciqual_columns import ALIM_CODE_COLUMN, ALIM_NAME_COLUMN, CIQUAL_COLUMNS
from app.importers.curated import import_curated, load_curated
from app.services.search import search

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "importers"))
from test_curated_parse import ff, make_dir

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def engine():
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"test_curated_{uuid.uuid4().hex[:12]}"
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


DISHES = [
    {
        "name": "Omelette", "name_en": "Omelette", "cuisine": "French", "source_url": "https://x",
        "ingredients": [{"alim_code": 22000, "grams": 120.0}, {"alim_code": 13039, "grams": 50}],
    }
]


def _setup(conn, tmp_path, **kw):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([ALIM_CODE_COLUMN, ALIM_NAME_COLUMN, *CIQUAL_COLUMNS.values()])
    for code, name in (("22000", "Oeuf, cru"), ("13039", "Pomme, crue")):
        ws.append([code, name, *("-" for _ in CIQUAL_COLUMNS)])
    wb.save(tmp_path / "ciqual.xlsx")
    import_ciqual(conn, tmp_path / "ciqual.xlsx")
    kw.setdefault("dishes", DISHES)
    return make_dir(tmp_path, **kw)


def _counts(conn):
    return [
        conn.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar_one()
        for t in ("product", "portion", "meal_template", "meal_template_item", "profile_revision")
    ]


def test_import_twice_same_counts(conn, tmp_path):
    d = _setup(conn, tmp_path)
    r1 = import_curated(conn, load_curated(d))
    c1 = _counts(conn)
    r2 = import_curated(conn, load_curated(d))
    assert _counts(conn) == c1
    assert r1 == r2
    assert (r1.names_set, r1.portions, r1.fastfood, r1.templates) == (2, 1, 1, 1)


def test_reimport_updates_changed_item(conn, tmp_path):
    d = _setup(conn, tmp_path)
    import_curated(conn, load_curated(d))
    pid = conn.execute(sa.text("SELECT id FROM product WHERE name = 'Big Mac'")).scalar_one()
    rev1, _ = catalogue.active_values(conn, pid)
    make_dir(tmp_path, dishes=DISHES, fastfood=[ff(kcal="520")])
    import_curated(conn, load_curated(d))
    rev2, values = catalogue.active_values(conn, pid)
    assert rev2 != rev1
    assert values[Nutrient.ENERGY_KCAL].range.lower.quantize(Decimal("0.001")) == Decimal("237.443")
    count = conn.execute(sa.text("SELECT count(*) FROM product WHERE name = 'Big Mac'")).scalar_one()
    assert count == 1


def test_fastfood_has_brand_portion_and_label_source(conn, tmp_path):
    import_curated(conn, load_curated(_setup(conn, tmp_path)))
    row = conn.execute(
        sa.text("SELECT id, brand, owner FROM product WHERE name = 'Big Mac'")
    ).one()
    assert (row.brand, row.owner) == ("McDonald's", "catalogue")
    amount = conn.execute(
        sa.text("SELECT amount FROM portion WHERE product_id = :p AND kind = 'serving'"),
        {"p": row.id},
    ).scalar_one()
    assert amount == Decimal(219)
    src = conn.execute(
        sa.text("SELECT source, external_id FROM source_record WHERE product_id = :p"),
        {"p": row.id},
    ).one()
    assert (src.source, src.external_id) == (Source.LABEL.value, "McDonald's:Big Mac")
    _, values = catalogue.active_values(conn, row.id)
    assert values[Nutrient.CARBOHYDRATE].known is False


def test_portions_attached_to_ciqual_products(conn, tmp_path):
    import_curated(conn, load_curated(_setup(conn, tmp_path)))
    row = conn.execute(
        sa.text(
            "SELECT po.label, po.amount, po.kind FROM portion po "
            "JOIN product p ON p.id = po.product_id WHERE p.name = 'Pomme, crue'"
        )
    ).one()
    assert (row.label, row.amount, row.kind) == ("1 medium apple", Decimal(182), "serving")


def test_name_en_searchable(conn, tmp_path):
    import_curated(conn, load_curated(_setup(conn, tmp_path)))
    assert "Oeuf, cru" in [r["name"] for r in search(conn, "egg")]


def test_templates_replace_items_and_ciqual_stays_live(conn, tmp_path):
    d = _setup(conn, tmp_path)
    import_curated(conn, load_curated(d))
    dish = {**DISHES[0], "ingredients": [{"alim_code": 22000, "grams": 100}], "estimate": True}
    make_dir(tmp_path, dishes=[dish])
    import_curated(conn, load_curated(d))
    assert _counts(conn)[2:4] == [1, 1]
    # Ciqual products stay live after the curated release is activated (carry-forward).
    assert search(conn, "pomme")


def test_ciqual_after_curated_keeps_fastfood_active(conn, tmp_path):
    d = _setup(conn, tmp_path)
    import_curated(conn, load_curated(d))
    import_ciqual(conn, tmp_path / "ciqual.xlsx")
    assert "Big Mac" in [r["name"] for r in search(conn, "big mac")]


def test_new_release_preserves_retired_and_merged_states(conn, tmp_path):
    from app.domain.identity import ProductState, Status

    import_curated(conn, load_curated(_setup(conn, tmp_path)))
    a, b = (
        conn.execute(sa.text("SELECT id FROM product WHERE name = :n"), {"n": n}).scalar_one()
        for n in ("Oeuf, cru", "Pomme, crue")
    )
    rev = {p: catalogue.active_values(conn, p)[0] for p in (a, b)}
    rel = catalogue.create_release(conn, "states")
    catalogue.set_product_state(conn, rel, a, ProductState(Status.RETIRED), rev[a])
    catalogue.set_product_state(conn, rel, b, ProductState(Status.MERGED, merged_into=a), rev[b])
    catalogue.activate_release(conn, rel)
    before = catalogue.load_states(conn)
    catalogue.activate_release(conn, catalogue.create_release(conn, "copy"))
    assert catalogue.load_states(conn) == before
    assert before[a].status == Status.RETIRED and before[b].status == Status.MERGED
