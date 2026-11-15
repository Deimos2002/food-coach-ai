"""Migration 0007: English product names and meal templates (food data pack, Task 2)."""

from __future__ import annotations

import os
import secrets
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.db import catalogue
from app.domain.quantity import Unit
from app.services import privacy

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
ROLE = "foodcoach_app"


def _db_url(name: str, user: str | None = None, password: str | None = None) -> str:
    url = make_url(os.environ["TEST_DATABASE_URL"]).set(database=name)
    if user is not None:
        url = url.set(username=user, password=password)
    return url.render_as_string(hide_password=False)


def _server():
    return sa.create_engine(os.environ["TEST_DATABASE_URL"], isolation_level="AUTOCOMMIT")


def _create_db() -> str:
    name = f"test_food_data_{uuid.uuid4().hex[:12]}"
    eng = _server()
    with eng.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    eng.dispose()
    return name


def _drop_db(name: str) -> None:
    eng = _server()
    with eng.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    eng.dispose()


def _cfg(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture
def db_name():
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL not set")
    name = _create_db()
    command.upgrade(_cfg(_db_url(name)), "head")
    try:
        yield name
    finally:
        _drop_db(name)


@pytest.fixture
def admin(db_name):
    eng = sa.create_engine(_db_url(db_name))
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_template(conn) -> int:
    pid = catalogue.insert_product(
        conn,
        owner="catalogue",
        kind="product",
        name="Riz",
        brand=None,
        basis_unit=Unit.G,
        state="as_sold",
    )
    tid = conn.execute(
        sa.text(
            "INSERT INTO meal_template (name, name_en, cuisine, source_url) "
            "VALUES ('Riz', 'Rice', 'fr', 'http://x') RETURNING id"
        )
    ).scalar_one()
    conn.execute(
        sa.text(
            "INSERT INTO meal_template_item (template_id, product_id, grams, position) "
            "VALUES (:t, :p, 100, 0)"
        ),
        {"t": tid, "p": pid},
    )
    return tid


def _shape(conn) -> tuple:
    tables = set(
        conn.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        ).scalars()
    )
    cols = {
        (t, c)
        for t, c in conn.execute(
            sa.text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        )
    }
    return tables, cols


def test_upgrade_adds_tables_and_column(admin):
    with admin.connect() as c:
        tables, cols = _shape(c)
    assert {"meal_template", "meal_template_item"} <= tables
    assert ("product", "name_en") in cols
    assert ("meal_item", "dish") in cols
    assert {"id", "name", "name_en", "cuisine", "chain", "estimate", "source_url"} <= {
        col for t, col in cols if t == "meal_template"
    }
    with admin.begin() as c:
        _seed_template(c)
        with pytest.raises(sa.exc.IntegrityError):
            c.execute(
                sa.text(
                    "INSERT INTO meal_template_item (template_id, product_id, grams, position) "
                    "SELECT template_id, product_id, 0, 1 FROM meal_template_item"
                )
            )


def test_app_role_can_read_templates(db_name, admin):
    with admin.begin() as c:
        _seed_template(c)
    password = secrets.token_hex(16)
    server = _server()
    with server.connect() as c:
        c.execute(sa.text(f"ALTER ROLE {ROLE} LOGIN PASSWORD '{password}'"))
    eng = sa.create_engine(_db_url(db_name, ROLE, password))
    try:
        with eng.connect() as c:
            assert c.execute(sa.text("SELECT count(*) FROM meal_template")).scalar_one() == 1
            assert c.execute(sa.text("SELECT count(*) FROM meal_template_item")).scalar_one() == 1
    finally:
        eng.dispose()
        with server.connect() as c:
            c.execute(sa.text(f"ALTER ROLE {ROLE} NOLOGIN PASSWORD NULL"))
        server.dispose()


def test_privacy_delete_keeps_templates(admin, tmp_path):
    with admin.begin() as c:
        _seed_template(c)
    (tmp_path / privacy.LEDGER_MARKER).touch()
    privacy.delete_all_data(admin, "owner", tmp_path / "deletion_ledger.jsonl")
    with admin.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM meal_template")).scalar_one() == 1
        assert c.execute(sa.text("SELECT count(*) FROM meal_template_item")).scalar_one() == 1


def test_downgrade_then_upgrade(db_name):
    cfg = _cfg(_db_url(db_name))
    command.downgrade(cfg, "0006_app_role")
    eng = sa.create_engine(_db_url(db_name))
    with eng.connect() as c:
        tables, cols = _shape(c)
    assert "meal_template" not in tables and "meal_template_item" not in tables
    assert ("product", "name_en") not in cols and ("meal_item", "dish") not in cols
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        tables, cols = _shape(c)
        assert {"meal_template", "meal_template_item"} <= tables
        assert ("product", "name_en") in cols and ("meal_item", "dish") in cols
    eng.dispose()
