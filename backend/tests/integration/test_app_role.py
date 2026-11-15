"""Migration 0006: the non-admin application role foodcoach_app (deployment Task 3, spec §3.6)."""

from __future__ import annotations

import os
import secrets
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.db import catalogue
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.domain.revisions import Action
from app.domain.values import Nutrient, Source

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
    name = f"test_app_role_{uuid.uuid4().hex[:12]}"
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


@pytest.fixture(scope="module")
def db_name():
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL not set")
    name = _create_db()
    command.upgrade(_cfg(_db_url(name)), "head")
    try:
        yield name
    finally:
        _drop_db(name)


@pytest.fixture(scope="module")
def admin(db_name):
    eng = sa.create_engine(_db_url(db_name))
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="module")
def app_engine(db_name):
    """Connect as foodcoach_app with a throwaway password; restore NOLOGIN afterwards."""
    password = secrets.token_hex(16)
    server = _server()
    with server.connect() as c:
        c.execute(sa.text(f"ALTER ROLE {ROLE} LOGIN PASSWORD '{password}'"))
    eng = sa.create_engine(_db_url(db_name, ROLE, password))
    try:
        yield eng
    finally:
        eng.dispose()
        with server.connect() as c:
            c.execute(sa.text(f"ALTER ROLE {ROLE} NOLOGIN PASSWORD NULL"))
        server.dispose()


@pytest.fixture(scope="module")
def product(admin):
    """A catalogue product with a live revision, seeded by the admin."""
    with admin.begin() as c:
        pid = catalogue.insert_product(
            c,
            owner="catalogue",
            kind="product",
            name="Pomme",
            brand=None,
            basis_unit=Unit.G,
            state="as_sold",
        )
        release = catalogue.create_release(c, "r")
        outcome = catalogue.add_source_revision(
            c,
            pid,
            Source.CIQUAL,
            "x",
            {},
            {Nutrient.ENERGY_KCAL: catalogue.NutrientValue.point(Decimal(52), Source.CIQUAL)},
        )
        assert outcome.decision.action is Action.ADOPT
        catalogue.set_product_state(
            c, release, pid, ProductState(Status.ACTIVE), outcome.live_revision_id
        )
        catalogue.activate_release(c, release)
    return pid, outcome.live_revision_id


def _insert_meal_item(c, product_id: int, revision_id: int) -> int:
    meal_id = c.execute(
        sa.text(
            "INSERT INTO meal (day, meal_type, data_generation) "
            "VALUES (CURRENT_DATE, 'lunch', 1) RETURNING id"
        )
    ).scalar_one()
    return c.execute(
        sa.text(
            """
            INSERT INTO meal_item (
                meal_id, requested_product_id, resolved_product_id, release_id,
                profile_revision_id, qty_kind, qty_value, qty_lower, qty_upper,
                unit, qty_source, nutrients, logged_at, data_generation
            ) VALUES (
                :meal_id, :pid, :pid, NULL, :rid, 'recorded', 100, 100, 100,
                'g', 'weighed', '{}', now(), 1
            ) RETURNING id
            """
        ),
        {"meal_id": meal_id, "pid": product_id, "rid": revision_id},
    ).scalar_one()


def test_app_role_dml_allowed(app_engine, product):
    with app_engine.begin() as c:
        item_id = _insert_meal_item(c, *product)
        assert c.execute(
            sa.text("SELECT qty_value FROM meal_item WHERE id = :i"), {"i": item_id}
        ).scalar_one() == Decimal(100)
        c.execute(
            sa.text(
                "UPDATE meal_item SET qty_value = 150, qty_lower = 150, qty_upper = 150 WHERE id = :i"
            ),
            {"i": item_id},
        )
        c.execute(sa.text("DELETE FROM meal_item WHERE id = :i"), {"i": item_id})
        assert (
            c.execute(
                sa.text("SELECT count(*) FROM meal_item WHERE id = :i"), {"i": item_id}
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("ddl", ["CREATE TABLE x (i int)", "ALTER TABLE product ADD COLUMN y int"])
def test_app_role_ddl_denied(app_engine, ddl):
    with app_engine.connect() as c:
        with pytest.raises(sa.exc.ProgrammingError) as err:
            c.execute(sa.text(ddl))
        assert err.value.orig.sqlstate in ("42501",)  # insufficient_privilege
        c.rollback()


def test_app_role_cannot_write_alembic_version(app_engine):
    with app_engine.connect() as c:
        with pytest.raises(sa.exc.ProgrammingError) as err:
            c.execute(sa.text("UPDATE alembic_version SET version_num = version_num"))
        assert err.value.orig.sqlstate == "42501"
        c.rollback()


def test_app_role_can_use_sequences(app_engine):
    with app_engine.begin() as c:
        new_id = c.execute(
            sa.text(
                "INSERT INTO meal (day, meal_type, data_generation) "
                "VALUES (CURRENT_DATE, 'dinner', 1) RETURNING id"
            )
        ).scalar_one()
        assert new_id is not None
        # A serial column draws from a real sequence (needs USAGE on it).
        seqs = (
            c.execute(
                sa.text(
                    "SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public'"
                )
            )
            .scalars()
            .all()
        )
        for seq in seqs:
            c.execute(sa.text(f"SELECT nextval('\"{seq}\"')"))


def test_app_role_can_write_table_created_later(admin, app_engine):
    # Simulates a later migration (0007) run by the admin role.
    with admin.begin() as c:
        c.execute(sa.text("CREATE TABLE later_table (id serial PRIMARY KEY, v int)"))
    with app_engine.begin() as c:
        c.execute(sa.text("INSERT INTO later_table (v) VALUES (1)"))
        assert c.execute(sa.text("SELECT v FROM later_table")).scalar_one() == 1


def test_downgrade_then_upgrade():
    name = _create_db()
    try:
        cfg = _cfg(_db_url(name))
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0005_coach")
        command.upgrade(cfg, "head")
        eng = sa.create_engine(_db_url(name))
        with eng.connect() as c:
            assert c.execute(
                sa.text("SELECT has_table_privilege(:r, 'meal_item', 'INSERT')"), {"r": ROLE}
            ).scalar_one()
        eng.dispose()
    finally:
        _drop_db(name)


def _privileges(conn) -> list:
    return conn.execute(
        sa.text(
            "SELECT c.relname, c.relacl::text FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' ORDER BY c.relname"
        )
    ).all() + conn.execute(
        sa.text("SELECT defaclobjtype::text, defaclacl::text FROM pg_default_acl ORDER BY 1")
    ).all() + conn.execute(
        sa.text("SELECT nspacl::text FROM pg_namespace WHERE nspname = 'public'")
    ).all()


def test_statements_idempotent(db_name):
    from app.db.app_role import APP_ROLE, app_role_statements

    assert APP_ROLE == ROLE
    assert not any("PASSWORD" in s.upper() for s in app_role_statements())
    eng = sa.create_engine(_db_url(db_name))
    try:
        with eng.connect() as c:
            before = _privileges(c)
        for _ in range(2):
            with eng.begin() as c:
                for stmt in app_role_statements():
                    c.execute(sa.text(stmt))
        with eng.connect() as c:
            assert _privileges(c) == before
    finally:
        eng.dispose()
