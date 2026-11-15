"""Catalogue persistence against a real Postgres (Task 8).

A fresh database is created per test module and migrated with ``alembic upgrade head``.
Each test runs inside a transaction that is rolled back afterwards.

The live revision of a product is defined only by ``product_state`` (spec A.6): the
user-owned row for user products, the active release's row for catalogue products.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.db import catalogue
from app.db.engine import make_engine
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.domain.revisions import Action
from app.domain.values import Nutrient, NutrientValue, Qualifier, Range, Source

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def engine():
    # Module scope cannot depend on the function-scoped ``db_url`` fixture, so the same
    # environment variable is read here (and the conftest skips integration tests without it).
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"test_catalogue_{uuid.uuid4().hex[:12]}"
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


def _product(conn, name: str = "Red Bull Pêche", kind: str = "product") -> int:
    return catalogue.insert_product(
        conn,
        owner="catalogue",
        kind=kind,
        name=name,
        brand="Red Bull",
        basis_unit=Unit.ML,
        state="as_sold",
    )


def _user_product(conn, name: str = "Mon yaourt", kind: str = "product") -> int:
    return catalogue.insert_product(
        conn,
        owner="user",
        kind=kind,
        name=name,
        brand=None,
        basis_unit=Unit.G,
        state="as_sold",
        data_generation=1,
    )


def _p(v: str) -> NutrientValue:
    return NutrientValue.point(Decimal(v), Source.OFF_CONTRIBUTOR)


def _publish(conn, product_id: int, revision_id: int) -> int:
    """What an importer does: put the product into a new release and activate it."""
    release = catalogue.create_release(conn, "test release")
    catalogue.set_product_state(conn, release, product_id, ProductState(Status.ACTIVE), revision_id)
    catalogue.activate_release(conn, release)
    return release


def _adopt(conn, *args) -> int:
    """Record a source revision that must be adopted; return the revision to place."""
    outcome = catalogue.add_source_revision(conn, *args)
    assert outcome.decision.action is Action.ADOPT
    assert outcome.pending_revision_id is None
    assert outcome.live_revision_id is not None
    return outcome.live_revision_id


def _revision_count(conn, product_id: int) -> int:
    return conn.execute(
        sa.text("SELECT count(*) FROM profile_revision WHERE product_id = :p"), {"p": product_id}
    ).scalar_one()


# --- The brief's three tests -----------------------------------------------------------------


def test_revision_adopt_then_review(conn):
    pid = _product(conn)
    first = {Nutrient.ENERGY_KCAL: _p("46"), Nutrient.SUGARS: _p("11")}
    rev1 = _adopt(conn, pid, Source.OFF_CONTRIBUTOR, "9002490100070", {"v": 1}, first)
    _publish(conn, pid, rev1)

    second = {Nutrient.ENERGY_KCAL: _p("46"), Nutrient.SUGARS: _p("0")}
    outcome = catalogue.add_source_revision(
        conn, pid, Source.OFF_CONTRIBUTOR, "9002490100070", {"v": 2}, second
    )
    assert outcome.decision.action is Action.REVIEW
    assert outcome.live_revision_id == rev1
    rev2 = outcome.pending_revision_id
    assert rev2 is not None and rev2 != rev1

    active_id, values = catalogue.active_values(conn, pid)
    assert active_id == rev1
    assert values[Nutrient.SUGARS].range == Range(Decimal(11), Decimal(11))

    status = conn.execute(
        sa.text("SELECT status, reasons FROM profile_revision WHERE id = :id"), {"id": rev2}
    ).one()
    assert status.status == "pending_review"
    assert status.reasons and "sugars" in status.reasons[0]


def test_a8_no_stitching(conn):
    pid = _product(conn)
    rev1 = _adopt(
        conn,
        pid,
        Source.OFF_CONTRIBUTOR,
        "x",
        {"v": 1},
        {Nutrient.ENERGY_KCAL: _p("100"), Nutrient.PROTEIN: _p("5")},
    )
    _publish(conn, pid, rev1)
    outcome = catalogue.add_source_revision(
        conn,
        pid,
        Source.OFF_CONTRIBUTOR,
        "x",
        {"v": 2},
        {Nutrient.ENERGY_KCAL: _p("200"), Nutrient.PROTEIN: _p("5"), Nutrient.SUGARS: _p("9")},
    )
    assert outcome.decision.action is Action.REVIEW
    assert Nutrient.SUGARS not in catalogue.active_values(conn, pid)[1]


def test_rollback_restores_catalogue_state(conn):
    p10 = _product(conn, "Produit A")
    p11 = _product(conn, "Produit B")
    r10 = _adopt(conn, p10, Source.CIQUAL, "10", {}, {Nutrient.PROTEIN: _p("1")})
    r11 = _adopt(conn, p11, Source.CIQUAL, "11", {}, {Nutrient.PROTEIN: _p("2")})

    release1 = catalogue.create_release(conn, "release 1")
    catalogue.set_product_state(conn, release1, p10, ProductState(Status.ACTIVE), r10)
    catalogue.set_product_state(conn, release1, p11, ProductState(Status.ACTIVE), r11)

    release2 = catalogue.create_release(conn, "release 2")
    catalogue.set_product_state(
        conn, release2, p10, ProductState(Status.MERGED, merged_into=p11), r10
    )
    catalogue.set_product_state(conn, release2, p11, ProductState(Status.ACTIVE), r11)

    catalogue.activate_release(conn, release2)
    merged = catalogue.load_states(conn)[p10]
    assert merged.status is Status.MERGED
    assert merged.merged_into == p11

    catalogue.activate_release(conn, release1)
    assert catalogue.load_states(conn)[p10].status is Status.ACTIVE
    assert catalogue.load_states(conn)[p10].merged_into is None


# --- Release pointer is the only notion of "active" (fix round 1) ---------------------------


def test_refetch_before_new_release_reuses_revision(conn):
    pid = _product(conn)
    r1 = _adopt(conn, pid, Source.OFF_CONTRIBUTOR, "x", {"v": 1}, {Nutrient.SUGARS: _p("11")})
    _publish(conn, pid, r1)

    v2 = {Nutrient.SUGARS: _p("11.5")}
    r2 = _adopt(conn, pid, Source.OFF_CONTRIBUTOR, "x", {"v": 2}, v2)
    assert r2 != r1
    # A catalogue ADOPT writes no state: the live release still points at r1.
    assert catalogue.active_values(conn, pid)[0] == r1

    r3 = _adopt(conn, pid, Source.OFF_CONTRIBUTOR, "x", {"v": 3}, v2)
    assert r3 == r2
    assert _revision_count(conn, pid) == 2


def test_rollback_to_release_without_row_has_no_active_revision(conn):
    pid = _product(conn)
    r1 = _adopt(conn, pid, Source.CIQUAL, "1", {}, {Nutrient.PROTEIN: _p("1")})
    release1 = catalogue.create_release(conn, "without P")
    release2 = catalogue.create_release(conn, "with P")
    catalogue.set_product_state(conn, release2, pid, ProductState(Status.ACTIVE), r1)

    catalogue.activate_release(conn, release2)
    assert catalogue.active_values(conn, pid)[0] == r1

    catalogue.activate_release(conn, release1)
    with pytest.raises(LookupError):
        catalogue.active_values(conn, pid)
    assert pid not in catalogue.load_states(conn)


def test_catalogue_product_without_state_has_no_active_revision(conn):
    pid = _product(conn)
    catalogue.add_source_revision(conn, pid, Source.CIQUAL, "1", {}, {Nutrient.PROTEIN: _p("1")})
    with pytest.raises(LookupError):
        catalogue.active_values(conn, pid)


def test_pending_revision_is_deduplicated(conn):
    pid = _product(conn)
    r1 = _adopt(conn, pid, Source.OFF_CONTRIBUTOR, "x", {}, {Nutrient.SUGARS: _p("11")})
    _publish(conn, pid, r1)
    conflicting = {Nutrient.SUGARS: _p("0")}
    o1 = catalogue.add_source_revision(
        conn, pid, Source.OFF_CONTRIBUTOR, "x", {"try": 1}, conflicting
    )
    o2 = catalogue.add_source_revision(
        conn, pid, Source.OFF_CONTRIBUTOR, "x", {"try": 2}, conflicting
    )
    assert o1.decision.action is Action.REVIEW and o2.decision.action is Action.REVIEW
    assert o1.live_revision_id == o2.live_revision_id == r1
    assert o1.pending_revision_id is not None
    assert o1.pending_revision_id == o2.pending_revision_id
    assert _revision_count(conn, pid) == 2
    assert catalogue.active_values(conn, pid)[0] == r1


def _status(conn, revision_id: int) -> str:
    return conn.execute(
        sa.text("SELECT status FROM profile_revision WHERE id = :id"), {"id": revision_id}
    ).scalar_one()


def _reject(conn, revision_id: int) -> None:
    conn.execute(
        sa.text("UPDATE profile_revision SET status = 'rejected' WHERE id = :id"),
        {"id": revision_id},
    )


def _reimport_into_new_release(conn, pid: int, values) -> catalogue.RevisionOutcome:
    """What an importer does on refresh: record, place the live revision, activate."""
    release = catalogue.create_release(conn, "refresh")
    outcome = catalogue.add_source_revision(conn, pid, Source.CIQUAL, "1", {}, values)
    assert outcome.live_revision_id is not None
    catalogue.set_product_state(
        conn, release, pid, ProductState(Status.ACTIVE), outcome.live_revision_id
    )
    catalogue.activate_release(conn, release)
    return outcome


def test_noop_reimport_keeps_product_live(conn):
    pid = _product(conn)
    r1 = _adopt(conn, pid, Source.CIQUAL, "1", {}, {Nutrient.PROTEIN: _p("12.5")})
    _publish(conn, pid, r1)

    outcome = _reimport_into_new_release(conn, pid, {Nutrient.PROTEIN: _p("12.50")})
    assert outcome.decision.action is Action.NOOP
    assert outcome.live_revision_id == r1
    assert outcome.pending_revision_id is None
    assert catalogue.active_values(conn, pid)[0] == r1
    assert catalogue.load_states(conn)[pid].status is Status.ACTIVE


def test_review_reimport_keeps_product_live(conn):
    pid = _product(conn)
    r1 = _adopt(conn, pid, Source.CIQUAL, "1", {}, {Nutrient.PROTEIN: _p("12.5")})
    _publish(conn, pid, r1)

    outcome = _reimport_into_new_release(conn, pid, {Nutrient.PROTEIN: _p("30")})
    assert outcome.decision.action is Action.REVIEW
    assert outcome.live_revision_id == r1
    assert outcome.pending_revision_id is not None and outcome.pending_revision_id != r1
    assert catalogue.active_values(conn, pid)[0] == r1
    assert pid in catalogue.load_states(conn)


def test_adopt_reimport_places_new_revision(conn):
    pid = _product(conn)
    r1 = _adopt(conn, pid, Source.CIQUAL, "1", {}, {Nutrient.PROTEIN: _p("12.5")})
    _publish(conn, pid, r1)

    outcome = _reimport_into_new_release(conn, pid, {Nutrient.PROTEIN: _p("13")})
    assert outcome.decision.action is Action.ADOPT
    assert outcome.live_revision_id not in (None, r1)
    assert outcome.pending_revision_id is None
    assert catalogue.active_values(conn, pid)[0] == outcome.live_revision_id


def test_rejected_match_never_goes_live(conn):
    pid = _product(conn)
    r1 = _adopt(conn, pid, Source.OFF_CONTRIBUTOR, "x", {}, {Nutrient.SUGARS: _p("11")})
    _publish(conn, pid, r1)
    v2 = {Nutrient.SUGARS: _p("11.5")}
    r2 = _adopt(conn, pid, Source.OFF_CONTRIBUTOR, "x", {}, v2)
    _reject(conn, r2)  # the user rejects r2 before any release places it

    outcome = catalogue.add_source_revision(conn, pid, Source.OFF_CONTRIBUTOR, "x", {}, v2)
    assert outcome.decision.action is Action.REVIEW
    assert outcome.live_revision_id == r1
    assert outcome.pending_revision_id == r2
    assert _status(conn, r2) == "rejected"
    assert _revision_count(conn, pid) == 2
    assert catalogue.active_values(conn, pid)[0] == r1


def test_rejected_match_on_user_product_does_not_move_user_state(conn):
    pid = _user_product(conn)
    r1 = _adopt(conn, pid, Source.MANUAL, "m", {}, {Nutrient.PROTEIN: _p("4")})
    r2 = _adopt(conn, pid, Source.MANUAL, "m", {}, {Nutrient.PROTEIN: _p("4.2")})
    catalogue.set_product_state(conn, None, pid, ProductState(Status.ACTIVE), r1)
    _reject(conn, r2)

    outcome = catalogue.add_source_revision(
        conn, pid, Source.MANUAL, "m", {}, {Nutrient.PROTEIN: _p("4.2")}
    )
    assert outcome.decision.action is Action.REVIEW
    assert outcome.live_revision_id == r1
    assert outcome.pending_revision_id == r2
    assert catalogue.active_values(conn, pid)[0] == r1


def test_review_dedup_never_downgrades_adopted(conn):
    pid = _user_product(conn)
    r1 = _adopt(conn, pid, Source.MANUAL, "m", {}, {Nutrient.PROTEIN: _p("4")})
    catalogue.set_product_state(conn, None, pid, ProductState(Status.ACTIVE), r1)
    _adopt(conn, pid, Source.MANUAL, "m", {}, {Nutrient.PROTEIN: _p("4.8")})  # +20%: adopt
    r3 = _adopt(conn, pid, Source.MANUAL, "m", {}, {Nutrient.PROTEIN: _p("5.7")})  # +18.75%
    assert catalogue.active_values(conn, pid)[0] == r3

    # Back to r1's data: 5.7 -> 4 is a >20% change, so REVIEW, matching adopted r1.
    outcome = catalogue.add_source_revision(
        conn, pid, Source.MANUAL, "m", {}, {Nutrient.PROTEIN: _p("4")}
    )
    assert outcome.decision.action is Action.REVIEW
    assert outcome.live_revision_id == r3
    assert outcome.pending_revision_id == r1
    assert _status(conn, r1) == "adopted"
    assert catalogue.active_values(conn, pid)[0] == r3


def test_noop_stores_nothing(conn):
    pid = _product(conn)
    r1 = _adopt(conn, pid, Source.OFF_CONTRIBUTOR, "x", {"v": 1}, {Nutrient.SUGARS: _p("11")})
    _publish(conn, pid, r1)
    outcome = catalogue.add_source_revision(
        conn, pid, Source.OFF_CONTRIBUTOR, "x", {"v": 2}, {Nutrient.SUGARS: _p("11.0")}
    )
    assert outcome.decision.action is Action.NOOP
    assert outcome.live_revision_id == r1
    assert outcome.pending_revision_id is None
    count = conn.execute(
        sa.text("SELECT count(*) FROM source_record WHERE product_id = :p"), {"p": pid}
    ).scalar_one()
    assert count == 1


def test_user_adopt_moves_user_state_and_roundtrips_values(conn):
    pid = _user_product(conn)
    rev1 = _adopt(conn, pid, Source.MANUAL, "m", {}, {Nutrient.PROTEIN: _p("12.5")})
    catalogue.set_product_state(conn, None, pid, ProductState(Status.ACTIVE), rev1)

    trace = NutrientValue(True, Range(Decimal(0), None), Qualifier.TRACE, Source.MANUAL)
    second = {
        Nutrient.PROTEIN: NutrientValue.point(Decimal(13), Source.MANUAL),
        Nutrient.SALT: trace,
        Nutrient.CAFFEINE: NutrientValue.unknown(Source.MANUAL),
    }
    rev2 = _adopt(conn, pid, Source.MANUAL, "m", {}, second)

    active_id, values = catalogue.active_values(conn, pid)
    assert active_id == rev2
    assert values == second
    assert pid in catalogue.load_states(conn)
    statuses = conn.execute(
        sa.text("SELECT status FROM profile_revision WHERE product_id = :p"), {"p": pid}
    ).scalars()
    assert set(statuses) == {"adopted"}


# --- set_product_state upsert path ----------------------------------------------------------


def _state_rows(conn, product_id: int):
    return conn.execute(
        sa.text(
            "SELECT release_id, status, merged_into, successors, active_revision_id "
            "FROM product_state WHERE product_id = :p"
        ),
        {"p": product_id},
    ).all()


def test_set_product_state_upserts_user_row(conn):
    pid = _user_product(conn)
    r1 = _adopt(conn, pid, Source.MANUAL, "m", {}, {Nutrient.PROTEIN: _p("4")})
    r2 = _adopt(conn, pid, Source.MANUAL, "m", {}, {Nutrient.PROTEIN: _p("9")})
    assert r1 != r2
    catalogue.set_product_state(conn, None, pid, ProductState(Status.ACTIVE), r1)
    catalogue.set_product_state(conn, None, pid, ProductState(Status.RETIRED, successors=(7,)), r2)
    rows = _state_rows(conn, pid)
    assert len(rows) == 1
    assert rows[0].release_id is None
    assert rows[0].status == "retired"
    assert list(rows[0].successors) == [7]
    assert rows[0].active_revision_id == r2


def test_set_product_state_upserts_release_row(conn):
    pid = _product(conn, "A")
    other = _product(conn, "B")
    r1 = _adopt(conn, pid, Source.CIQUAL, "1", {}, {Nutrient.PROTEIN: _p("1")})
    release = catalogue.create_release(conn, "r")
    catalogue.set_product_state(conn, release, pid, ProductState(Status.ACTIVE), r1)
    catalogue.set_product_state(
        conn, release, pid, ProductState(Status.MERGED, merged_into=other), r1
    )
    rows = _state_rows(conn, pid)
    assert len(rows) == 1
    assert rows[0].release_id == release
    assert rows[0].status == "merged"
    assert rows[0].merged_into == other


# --- Schema guards --------------------------------------------------------------------------


def test_merged_requires_merged_into(conn):
    pid = _product(conn)
    rev = _adopt(conn, pid, Source.CIQUAL, "1", {}, {Nutrient.PROTEIN: _p("1")})
    release = catalogue.create_release(conn, "bad")
    with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
        catalogue.set_product_state(conn, release, pid, ProductState(Status.MERGED), rev)


def test_user_product_requires_data_generation(conn):
    with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
        conn.execute(
            sa.text(
                "INSERT INTO product (owner, kind, name, basis_unit, state, search_text) "
                "VALUES ('user', 'product', 'x', 'g', 'as_sold', 'x')"
            )
        )


def test_cooking_additions_only_for_user_dishes(conn):
    user_dish = _user_product(conn, "Curry maison", kind="dish")
    catalogue_dish = _product(conn, "Hachis parmentier", kind="dish")
    rows = dict(
        conn.execute(
            sa.text("SELECT id, includes_cooking_additions FROM product WHERE id IN (:a, :b)"),
            {"a": user_dish, "b": catalogue_dish},
        ).all()
    )
    assert rows == {user_dish: "unanswered", catalogue_dish: None}


def test_unknown_value_cannot_store_a_range(conn):
    pid = _product(conn)
    rev = _adopt(conn, pid, Source.CIQUAL, "1", {}, {Nutrient.PROTEIN: _p("1")})
    with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
        conn.execute(
            sa.text(
                "INSERT INTO nutrient_value (revision_id, nutrient, known, lower, upper, "
                "qualifier, source, derivation, verification) VALUES "
                "(:r, 'fat', false, 0, 0, 'none', 'ciqual', 'direct', 'unverified')"
            ),
            {"r": rev},
        )


def test_override_version_unique_per_product_nutrient(conn):
    pid = _user_product(conn)
    rev = _adopt(conn, pid, Source.MANUAL, "m", {}, {Nutrient.SALT: _p("1")})
    insert = sa.text(
        "INSERT INTO nutrient_override (version, product_id, nutrient, known, lower, upper, "
        "qualifier, source, derivation, verification, pinned_revision_id, reason, "
        "data_generation) VALUES (1, :p, 'salt', true, 0.5, 0.5, 'none', 'manual', 'direct', "
        "'user_verified', :r, 'label', 1)"
    )
    conn.execute(insert, {"p": pid, "r": rev})
    with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
        conn.execute(insert, {"p": pid, "r": rev})


def test_raw_json_numbers_come_back_as_decimal(conn):
    pid = _product(conn)
    catalogue.add_source_revision(
        conn, pid, Source.OFF_CONTRIBUTOR, "x", {"sugars_100g": 11.5}, {Nutrient.SUGARS: _p("1")}
    )
    raw = conn.execute(
        sa.select(catalogue.source_record.c.raw).where(catalogue.source_record.c.product_id == pid)
    ).scalar_one()
    assert raw == {"sugars_100g": Decimal("11.5")}
    assert isinstance(raw["sugars_100g"], Decimal)


def test_extensions_installed(conn):
    names = set(conn.execute(sa.text("SELECT extname FROM pg_extension")).scalars())
    assert {"pg_trgm", "unaccent"} <= names
