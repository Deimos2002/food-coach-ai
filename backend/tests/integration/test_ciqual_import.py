"""Ciqual importer against a real Postgres (Task 9).

Follows the fixture pattern of ``test_catalogue_db.py``: a fresh, module-scoped
database migrated to head with alembic, one test transaction per test.
"""

from __future__ import annotations

import os
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
from app.domain.values import Nutrient, Qualifier, Range
from app.importers.ciqual import import_ciqual
from app.importers.ciqual_columns import ALIM_CODE_COLUMN, ALIM_NAME_COLUMN, CIQUAL_COLUMNS

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


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


_HEADER = [ALIM_CODE_COLUMN, ALIM_NAME_COLUMN, *CIQUAL_COLUMNS.values()]


def _row(alim_code: str, name: str, protein: str, **overrides: str) -> list[str]:
    values = {col: "-" for col in CIQUAL_COLUMNS.values()}
    values[CIQUAL_COLUMNS[Nutrient.PROTEIN]] = protein
    values.update(overrides)
    return [alim_code, name, *(values[col] for col in CIQUAL_COLUMNS.values())]


def _write_fixture(path: Path, rows: list[list[str]]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_HEADER)
    for row in rows:
        ws.append(row)
    wb.save(path)


def _tiny_rows() -> list[list[str]]:
    return [
        _row("1", "Aliment un", "12,5"),
        _row("2", "Aliment deux", "traces"),
        _row("3", "Aliment trois, cuit", "-"),
    ]


def test_import_tiny_fixture(conn, tmp_path):
    xlsx = tmp_path / "tiny.xlsx"
    _write_fixture(xlsx, _tiny_rows())

    release_id = import_ciqual(conn, xlsx)
    assert isinstance(release_id, int)

    products = conn.execute(
        sa.text("SELECT id, name, state, basis_unit FROM product WHERE owner = 'catalogue' "
                 "ORDER BY id")
    ).all()
    assert len(products) == 3
    assert products[0].state == "as_sold"
    assert products[2].state == "as_prepared"  # name contains "cuit"
    assert {p.basis_unit for p in products} == {"g"}

    values_by_product = {}
    for p in products:
        _, values = catalogue.active_values(conn, p.id)
        values_by_product[p.name] = values[Nutrient.PROTEIN]

    point = values_by_product["Aliment un"]
    assert point.known is True
    assert point.range == Range(Decimal("12.5"), Decimal("12.5"))
    assert point.qualifier == Qualifier.NONE

    trace = values_by_product["Aliment deux"]
    assert trace.known is True
    assert trace.range == Range(Decimal(0), None)
    assert trace.qualifier == Qualifier.TRACE

    unknown = values_by_product["Aliment trois, cuit"]
    assert unknown.known is False
    assert unknown.range is None


def test_import_sets_active_release(conn, tmp_path):
    xlsx = tmp_path / "tiny.xlsx"
    _write_fixture(xlsx, _tiny_rows())
    release_id = import_ciqual(conn, xlsx)
    active = conn.execute(sa.text("SELECT release_id FROM active_release")).scalar_one()
    assert active == release_id


def test_reimport_is_idempotent_on_product_count(conn, tmp_path):
    xlsx = tmp_path / "tiny.xlsx"
    _write_fixture(xlsx, _tiny_rows())

    import_ciqual(conn, xlsx)
    import_ciqual(conn, xlsx)

    count = conn.execute(
        sa.text("SELECT count(*) FROM product WHERE owner = 'catalogue'")
    ).scalar_one()
    assert count == 3


def test_malformed_cell_is_stored_unknown_and_counted(conn, tmp_path, capsys):
    xlsx = tmp_path / "bad.xlsx"
    rows = [
        _row("1", "Aliment valide", "12,5"),
        _row(
            "2",
            "Aliment avec cellule invalide",
            "pas un nombre",
        ),
    ]
    _write_fixture(xlsx, rows)

    import_ciqual(conn, xlsx)

    products = conn.execute(
        sa.text("SELECT id, name FROM product WHERE owner = 'catalogue' ORDER BY id")
    ).all()
    assert len(products) == 2
    bad = next(p for p in products if p.name == "Aliment avec cellule invalide")
    _, values = catalogue.active_values(conn, bad.id)
    assert values[Nutrient.PROTEIN].known is False

    captured = capsys.readouterr()
    assert "parse failures" in captured.out
    # Console encoding can mangle accented characters in the header text, so check an
    # unaccented fragment of the protein column header rather than the exact string.
    assert "N x" in captured.out
    assert ": 1" in captured.out
