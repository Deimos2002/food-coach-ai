"""Meals, idempotent item logging and day status against a real Postgres (Task 12).

A fresh database is created per test module and migrated with ``alembic upgrade head``.
The API commits its own transactions, so user tables are truncated after each test.
Authentication is overridden to a fixed ``CurrentUser``.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.api import routes_meals
from app.api.auth import CurrentUser, get_current_user
from app.config import Settings
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
    name = f"test_logging_{uuid.uuid4().hex[:12]}"
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
def client(engine):
    app = create_app(Settings())
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(oid="test-owner")
    app.dependency_overrides[routes_meals.get_db_engine] = lambda: engine
    try:
        yield TestClient(app)
    finally:
        with engine.begin() as c:
            c.execute(
                sa.text(
                    "TRUNCATE meal_item, meal, day, idempotency_key, product_state, "
                    "nutrient_override, nutrient_value, profile_revision, source_record, "
                    "product, active_release, catalogue_release, user_account "
                    "RESTART IDENTITY CASCADE"
                )
            )


def _p(v: str, source: Source = Source.CIQUAL) -> NutrientValue:
    return NutrientValue.point(Decimal(v), source)


def _catalogue_product(engine, name: str, basis: Unit, values) -> int:
    with engine.begin() as c:
        pid = catalogue.insert_product(
            c,
            owner="catalogue",
            kind="product",
            name=name,
            brand=None,
            basis_unit=basis,
            state="as_sold",
        )
        rev = catalogue.add_source_revision(c, pid, Source.CIQUAL, name, {}, values)
        release = catalogue.create_release(c, f"release {name}")
        # Carry every product already live into the new release.
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


def _user_product(engine, name: str, values) -> tuple[int, int]:
    with engine.begin() as c:
        pid = catalogue.insert_product(
            c,
            owner="user",
            kind="product",
            name=name,
            brand=None,
            basis_unit=Unit.G,
            state="as_sold",
            data_generation=1,
        )
        rev = catalogue.add_source_revision(c, pid, Source.MANUAL, "m", {}, values)
        catalogue.set_product_state(c, None, pid, ProductState(Status.ACTIVE), rev.live_revision_id)
    return pid, rev.live_revision_id


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


def _count_items(engine) -> int:
    with engine.connect() as c:
        return c.execute(sa.text("SELECT count(*) FROM meal_item")).scalar_one()


def test_a15_concurrent_same_key_one_item(client, engine):
    pid = _catalogue_product(engine, "Pomme", Unit.G, {Nutrient.SUGARS: _p("10")})
    meal = _meal(client)
    barrier = threading.Barrier(2)
    results: list = [None, None]

    def worker(i: int) -> None:
        barrier.wait()
        results[i] = _add(client, meal, pid, key="same-key")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r.status_code for r in results] == [201, 201]
    assert results[0].json() == results[1].json()
    assert results[0].json()["nutrients"]["sugars"]["lower"] == "10"
    assert _count_items(engine) == 1


def test_a16_same_key_different_payload_409(client, engine):
    pid = _catalogue_product(engine, "Pomme", Unit.G, {Nutrient.SUGARS: _p("10")})
    meal = _meal(client)
    assert _add(client, meal, pid, amount="100", key="k1").status_code == 201
    # Same payload with an equal Decimal spelled differently is the same request.
    assert _add(client, meal, pid, amount="100.0", key="k1").status_code == 201
    r = _add(client, meal, pid, amount="150", key="k1")
    assert r.status_code == 409
    assert _count_items(engine) == 1


def test_a25_grams_for_ml_product_422(client, engine):
    pid = _catalogue_product(engine, "Red Bull", Unit.ML, {Nutrient.SUGARS: _p("11")})
    meal = _meal(client)
    r = _add(client, meal, pid, amount="250", unit="g")
    assert r.status_code == 422
    assert _count_items(engine) == 0
    ok = _add(client, meal, pid, amount="250", unit="ml")
    assert ok.status_code == 201
    assert ok.json()["nutrients"]["sugars"] == {
        "known": True,
        "lower": "27.5",
        "upper": "27.5",
        "qualifier": "none",
    }


def test_review_focus_amount_validation(client, engine):
    pid = _catalogue_product(engine, "Pomme", Unit.G, {Nutrient.SUGARS: _p("10")})
    meal = _meal(client)
    for amount in ("0", "-5", "5001"):
        assert _add(client, meal, pid, amount=amount).status_code == 422, amount
    assert _add(client, meal, pid, amount="5000").status_code == 201
    assert _count_items(engine) == 1


def test_review_focus_client_local_date(client, engine):
    frozen = datetime(2026, 10, 12, 22, 30, tzinfo=UTC)  # 00:30 on 13 Oct in Paris
    client.app.dependency_overrides[routes_meals.get_now] = lambda: frozen
    pid = _catalogue_product(engine, "Pomme", Unit.G, {Nutrient.SUGARS: _p("10")})
    meal = _meal(client, day="2026-10-13", meal_type="snack")
    assert _add(client, meal, pid).status_code == 201
    with engine.connect() as c:
        day = c.execute(sa.text("SELECT day FROM meal WHERE id = :m"), {"m": meal}).scalar_one()
        logged_at = c.execute(sa.text("SELECT logged_at FROM meal_item")).scalar_one()
    assert day.isoformat() == "2026-10-13"
    assert logged_at == frozen
    r = client.get("/days/2026-10-13")
    assert r.status_code == 200
    assert len(r.json()["meals"]) == 1
    assert client.get("/days/2026-10-12").json()["meals"] == []


def test_a13_snapshot_pinned(client, engine):
    pid, rev1 = _user_product(engine, "Mon yaourt", {Nutrient.PROTEIN: _p("4", Source.MANUAL)})
    meal = _meal(client)
    r = _add(client, meal, pid, amount="200")
    assert r.status_code == 201
    item_id = r.json()["item_id"]
    before = r.json()["nutrients"]
    with engine.begin() as c:
        rev2 = catalogue.add_source_revision(
            c, pid, Source.MANUAL, "m", {}, {Nutrient.PROTEIN: _p("4.5", Source.MANUAL)}
        ).live_revision_id
        assert catalogue.active_values(c, pid)[0] == rev2 != rev1
    with engine.connect() as c:
        row = c.execute(
            sa.text("SELECT profile_revision_id, nutrients FROM meal_item WHERE id = :i"),
            {"i": item_id},
        ).one()
    assert row.profile_revision_id == rev1
    assert row.nutrients == before
    # Editing the quantity recomputes with the pinned revision, not the new live one.
    p = client.patch(f"/meal-items/{item_id}", json={"amount": "100"})
    assert p.status_code == 200, p.text
    assert p.json()["nutrients"]["protein"]["lower"] == "4"


def test_a17_delete_from_complete_day_partial(client, engine):
    pid = _catalogue_product(engine, "Pomme", Unit.G, {Nutrient.SUGARS: _p("10")})
    meal = _meal(client)
    assert client.get("/days/2026-10-12").json()["status"] == "not_logged"
    item = _add(client, meal, pid).json()["item_id"]
    _add(client, meal, pid)
    assert client.get("/days/2026-10-12").json()["status"] == "partial"
    assert client.put("/days/2026-10-12/status", json={"status": "complete"}).status_code == 200
    _add(client, meal, pid)  # adding keeps a complete day complete
    assert client.get("/days/2026-10-12").json()["status"] == "complete"
    assert client.delete(f"/meal-items/{item}").status_code == 204
    assert client.get("/days/2026-10-12").json()["status"] == "partial"
    assert _count_items(engine) == 2


def test_retired_product_cannot_be_logged_422(client, engine):
    successor = _catalogue_product(engine, "Nouveau", Unit.G, {Nutrient.SUGARS: _p("1")})
    old = _catalogue_product(engine, "Ancien", Unit.G, {Nutrient.SUGARS: _p("1")})
    with engine.begin() as c:
        rel = c.execute(sa.text("SELECT release_id FROM active_release")).scalar_one()
        rev = catalogue.active_values(c, old)[0]
        catalogue.set_product_state(
            c, rel, old, ProductState(Status.RETIRED, successors=(successor,)), rev
        )
    meal = _meal(client)
    r = _add(client, meal, old)
    assert r.status_code == 422
    assert r.json() == {"detail": "product retired", "successors": [successor]}
    assert _count_items(engine) == 0


def test_a1_day_endpoint_lists_unresolved_caffeine(client, engine):
    red_bull = _catalogue_product(
        engine,
        "Red Bull",
        Unit.ML,
        {Nutrient.SUGARS: _p("11"), Nutrient.CAFFEINE: NutrientValue.unknown(Source.CIQUAL)},
    )
    pomme = _catalogue_product(
        engine, "Pomme", Unit.G, {Nutrient.SUGARS: _p("10"), Nutrient.CAFFEINE: _p("0")}
    )
    meal = _meal(client)
    assert _add(client, meal, red_bull, amount="250", unit="ml").status_code == 201
    assert _add(client, meal, pomme, amount="150").status_code == 201
    body = client.get("/days/2026-10-12").json()
    caffeine = body["nutrients"]["caffeine"]
    assert caffeine["fields_complete"] is False
    assert "Red Bull" in caffeine["unresolved"]
    assert caffeine["recorded"] == {"lower": "0", "upper": "0"}
    assert body["nutrients"]["sugars"]["recorded"] == {"lower": "42.5", "upper": "42.5"}


# --- Final fixes C3: partial quantity PATCH merges with the stored row ------------------------


def _estimated_item(client, engine) -> int:
    pid = _catalogue_product(engine, "Riz", Unit.G, {Nutrient.CARBOHYDRATE: _p("28")})
    meal = _meal(client)
    r = _add(client, meal, pid, amount="200", lower="140", upper="260",
             quantity_source="photo_estimate")
    assert r.status_code == 201, r.text
    return r.json()["item_id"]


def _quantity_of(client, item_id: int) -> dict:
    body = client.get("/days/2026-10-12").json()
    (item,) = [i for m in body["meals"] for i in m["items"] if i["item_id"] == item_id]
    return item["quantity"]


def test_c3_patch_amount_only_keeps_estimate_bounds(client, engine):
    item = _estimated_item(client, engine)
    r = client.patch(f"/meal-items/{item}", json={"amount": "210"})
    assert r.status_code == 200, r.text
    q = _quantity_of(client, item)
    assert (q["kind"], q["value"], q["lower"], q["upper"]) == ("estimated_range", "210", "140", "260")
    assert r.json()["nutrients"]["carbohydrate"] == {
        "known": True, "lower": "39.2", "upper": "72.8", "qualifier": "none"
    }


def test_c3_patch_lower_only_keeps_upper(client, engine):
    item = _estimated_item(client, engine)
    r = client.patch(f"/meal-items/{item}", json={"lower": "150"})
    assert r.status_code == 200, r.text
    q = _quantity_of(client, item)
    assert (q["kind"], q["value"], q["lower"], q["upper"]) == ("estimated_range", "200", "150", "260")


def test_c3_patch_explicit_null_bounds_records_a_point(client, engine):
    item = _estimated_item(client, engine)
    r = client.patch(
        f"/meal-items/{item}",
        json={"amount": "180", "lower": None, "upper": None, "quantity_source": "weighed"},
    )
    assert r.status_code == 200, r.text
    q = _quantity_of(client, item)
    assert (q["kind"], q["value"], q["lower"], q["upper"]) == ("recorded", "180", "180", "180")


def test_c3_patch_merged_bounds_validated(client, engine):
    item = _estimated_item(client, engine)
    r = client.patch(f"/meal-items/{item}", json={"amount": "300"})
    assert r.status_code == 422
    assert "detail" in r.json()
    q = _quantity_of(client, item)
    assert (q["value"], q["lower"], q["upper"]) == ("200", "140", "260")


# --- Final fixes C1: the day view reads one snapshot -------------------------------------------


def test_c1_day_view_single_snapshot_against_concurrent_delete(client, engine):
    from app.services import logging as svc

    pid = _catalogue_product(engine, "Pomme", Unit.G, {Nutrient.SALT: _p("1")})
    meal = _meal(client)
    first = _add(client, meal, pid).json()["item_id"]
    _add(client, meal, pid)
    assert client.put("/days/2026-10-12/status", json={"status": "complete"}).status_code == 200

    fired = threading.Event()

    def delete_between_statements(conn, cursor, statement, params, context, executemany):
        # Right after the day view reads the day status, another transaction deletes an
        # item (which also makes the day partial) and commits.
        if fired.is_set() or "FROM day" not in statement or "FOR UPDATE" in statement:
            return
        fired.set()
        with engine.begin() as other:
            generation = other.execute(
                sa.text("SELECT data_generation FROM user_account")
            ).scalar_one()
            svc.delete_item(other, first, generation, datetime.now(UTC))

    sa.event.listen(engine, "after_cursor_execute", delete_between_statements)
    try:
        body = client.get("/days/2026-10-12").json()
    finally:
        sa.event.remove(engine, "after_cursor_execute", delete_between_statements)

    assert fired.is_set()
    items = [i for m in body["meals"] for i in m["items"]]
    # One snapshot: the status and the items agree (both from before the delete).
    assert (body["status"], len(items)) == ("complete", 2)
    assert body["nutrients"]["salt"]["recorded"] == {"lower": "2", "upper": "2"}
    assert client.get("/days/2026-10-12").json()["status"] == "partial"


# --- Final fixes C2: add-item reads one release ------------------------------------------------


def test_c2_release_activated_mid_add_item_does_not_mix(client, engine):
    pid = _catalogue_product(engine, "Yaourt", Unit.G, {Nutrient.SUGARS: _p("10")})
    with engine.begin() as c:
        r1 = c.execute(sa.text("SELECT release_id FROM active_release")).scalar_one()
        rev2 = catalogue.add_source_revision(
            c, pid, Source.CIQUAL, "v2", {}, {Nutrient.SUGARS: _p("10.5")}
        ).live_revision_id
        r2 = catalogue.create_release(c, "r2")
        catalogue.set_product_state(c, r2, pid, ProductState(Status.ACTIVE), rev2)
    meal = _meal(client)

    activator: list[threading.Thread] = []

    def activate_r2():
        with engine.begin() as other:
            catalogue.activate_release(other, r2)

    def activate_mid_request(conn, cursor, statement, params, context, executemany):
        # After add-item has read the product's live revision values, activate r2.
        if activator or "FROM nutrient_value" not in statement:
            return
        t = threading.Thread(target=activate_r2)
        activator.append(t)
        t.start()
        t.join(timeout=1)  # with the fix this waits on add-item's lock and times out

    sa.event.listen(engine, "after_cursor_execute", activate_mid_request)
    try:
        r = _add(client, meal, pid)
    finally:
        sa.event.remove(engine, "after_cursor_execute", activate_mid_request)
        for t in activator:
            t.join(timeout=10)
    assert r.status_code == 201, r.text
    assert activator

    with engine.connect() as c:
        item = c.execute(
            sa.text("SELECT release_id, profile_revision_id FROM meal_item WHERE id = :i"),
            {"i": r.json()["item_id"]},
        ).one()
        placed = c.execute(
            sa.text(
                "SELECT active_revision_id FROM product_state "
                "WHERE release_id = :r AND product_id = :p"
            ),
            {"r": item.release_id, "p": pid},
        ).scalar_one()
        assert c.execute(sa.text("SELECT release_id FROM active_release")).scalar_one() == r2
    # The item's revision is the one its recorded release places for the product.
    assert item.profile_revision_id == placed
    assert item.release_id == r1


# --- Final fixes F8: A14 ------------------------------------------------------------------------


def test_a14_rollback_keeps_logged_product_and_log(client, engine):
    """A refresh (r2) introduces a product, the user logs it, then r2 is rolled back to r1.

    Stage 1 part of A14: rollback only moves the release pointer, so the product row and
    the user's frozen item are kept. (Run-tagged rollback deletion is stage 2.)
    """
    _catalogue_product(engine, "Ancien", Unit.G, {Nutrient.SUGARS: _p("1")})
    with engine.connect() as c:
        r1 = c.execute(sa.text("SELECT release_id FROM active_release")).scalar_one()
    new = _catalogue_product(engine, "Nouveau", Unit.G, {Nutrient.SUGARS: _p("12")})
    meal = _meal(client)
    r = _add(client, meal, new, amount="50")
    assert r.status_code == 201, r.text
    item_id, logged = r.json()["item_id"], r.json()["nutrients"]

    with engine.begin() as c:
        catalogue.activate_release(c, r1)  # rollback: "Nouveau" is not in r1
        with pytest.raises(LookupError):
            catalogue.active_values(c, new)

    with engine.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM product WHERE id = :p"), {"p": new}).scalar_one() == 1
    (item,) = [i for m in client.get("/days/2026-10-12").json()["meals"] for i in m["items"]]
    assert item["item_id"] == item_id
    assert item["name"] == "Nouveau"
    assert item["nutrients"] == logged
    assert item["nutrients"]["sugars"]["lower"] == "6"


def _stored_dish(engine, item_id: int):
    with engine.connect() as c:
        return c.execute(
            sa.text("SELECT dish FROM meal_item WHERE id = :i"), {"i": item_id}
        ).scalar_one()


def test_item_create_stores_dish(client, engine):
    pid, _ = _user_product(engine, "Mon yaourt", {Nutrient.PROTEIN: _p("4", Source.MANUAL)})
    meal = _meal(client)
    r = _add(client, meal, pid, dish="Couscous royal")
    assert r.status_code == 201, r.text
    assert _stored_dish(engine, r.json()["item_id"]) == "Couscous royal"
    day = client.get("/days/2026-10-12").json()
    assert day["meals"][0]["items"][0]["dish"] == "Couscous royal"
    assert _add(client, meal, pid, dish="x" * 201).status_code == 422


def test_item_dish_optional(client, engine):
    pid, _ = _user_product(engine, "Mon yaourt", {Nutrient.PROTEIN: _p("4", Source.MANUAL)})
    r = _add(client, _meal(client), pid)
    assert r.status_code == 201, r.text
    assert _stored_dish(engine, r.json()["item_id"]) is None
