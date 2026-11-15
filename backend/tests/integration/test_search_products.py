"""Search and product endpoints against a real Postgres (Task 13).

A fresh database is created per test module and migrated with ``alembic upgrade head``.
Each test runs inside a transaction that is rolled back afterwards, with the FastAPI
app's DB dependency overridden to use that same connection.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import respx
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
from app.domain.revisions import Action
from app.domain.values import Nutrient, Source
from app.main import create_app
from app.services.search import normalize, search, search_text_for

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def engine():
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"test_search_{uuid.uuid4().hex[:12]}"
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


@pytest.fixture
def client(conn):
    app = create_app()

    def _override_conn():
        yield conn

    app.dependency_overrides[get_connection] = _override_conn
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(oid="owner")
    return TestClient(app)


def _catalogue_product(conn, name: str, brand: str | None = "Red Bull") -> int:
    return catalogue.insert_product(
        conn,
        owner="catalogue",
        kind="product",
        name=name,
        brand=brand,
        basis_unit=Unit.ML,
        state="as_sold",
    )


def _publish_all(conn, products: dict[int, dict]) -> dict[int, int]:
    """Record a revision per product and activate ONE release containing all of them.

    A catalogue release is a whole-catalogue snapshot (A.6): activating a release that
    only names one product would drop every other catalogue product out of "live".
    Returns the placed revision id per product.
    """
    release = catalogue.create_release(conn, "r")
    revision_ids: dict[int, int] = {}
    for product_id, values in products.items():
        outcome = catalogue.add_source_revision(conn, product_id, Source.CIQUAL, "x", {}, values)
        assert outcome.decision.action is Action.ADOPT
        catalogue.set_product_state(conn, release, product_id, ProductState(Status.ACTIVE), outcome.live_revision_id)
        revision_ids[product_id] = outcome.live_revision_id
    catalogue.activate_release(conn, release)
    return revision_ids


def _log_meal_item(conn, product_id: int, revision_id: int) -> None:
    """Insert a minimal, constraint-satisfying meal_item row (Task 12's real table)."""
    meal_id = conn.execute(
        sa.text(
            "INSERT INTO meal (day, meal_type, data_generation) "
            "VALUES (CURRENT_DATE, 'lunch', 1) RETURNING id"
        )
    ).scalar_one()
    conn.execute(
        sa.text(
            """
            INSERT INTO meal_item (
                meal_id, requested_product_id, resolved_product_id, release_id,
                profile_revision_id, qty_kind, qty_value, qty_lower, qty_upper,
                unit, qty_source, nutrients, logged_at, data_generation
            ) VALUES (
                :meal_id, :product_id, :product_id, NULL,
                :revision_id, 'recorded', 100, 100, 100,
                'g', 'weighed', '{}', now(), 1
            )
            """
        ),
        {"meal_id": meal_id, "product_id": product_id, "revision_id": revision_id},
    )


def test_redbull_variants_found(conn):
    p1 = _catalogue_product(conn, "Red Bull Pêche")
    p2 = _catalogue_product(conn, "Red Bull Pêche Sugarfree")
    _publish_all(
        conn,
        {
            p1: {Nutrient.ENERGY_KCAL: catalogue.NutrientValue.point(Decimal(46), Source.CIQUAL)},
            p2: {Nutrient.ENERGY_KCAL: catalogue.NutrientValue.point(Decimal(5), Source.CIQUAL)},
        },
    )

    results = search(conn, "redbull peche")
    ids = {r["product_id"] for r in results}
    assert {p1, p2} <= ids


def test_review_focus_short_query_empty(conn):
    assert search(conn, "") == []
    assert search(conn, "r") == []


def test_like_underscore_is_escaped_not_a_wildcard(conn):
    pid = _catalogue_product(conn, "axb", brand=None)
    _publish_all(
        conn, {pid: {Nutrient.ENERGY_KCAL: catalogue.NutrientValue.point(Decimal(1), Source.CIQUAL)}}
    )

    # Unescaped, "_" in the LIKE pattern is a single-char wildcard and "a_b" would match
    # "axb". Escaped, it's a literal underscore and must not match.
    results = search(conn, "a_b")
    assert pid not in {r["product_id"] for r in results}


def test_search_excludes_retired_product(conn):
    p1 = _catalogue_product(conn, "Compote Abricot", brand=None)
    p2 = _catalogue_product(conn, "Compote Abricot", brand=None)
    revisions = _publish_all(
        conn,
        {
            p1: {Nutrient.ENERGY_KCAL: catalogue.NutrientValue.point(Decimal(50), Source.CIQUAL)},
            p2: {Nutrient.ENERGY_KCAL: catalogue.NutrientValue.point(Decimal(50), Source.CIQUAL)},
        },
    )
    # Retire p2 in a new release: its live state row now has status 'retired', not 'active'.
    release = catalogue.create_release(conn, "retire p2")
    catalogue.set_product_state(conn, release, p1, ProductState(Status.ACTIVE), revisions[p1])
    catalogue.set_product_state(conn, release, p2, ProductState(Status.RETIRED), revisions[p2])
    catalogue.activate_release(conn, release)

    ids = {r["product_id"] for r in search(conn, "compote abricot")}
    assert p1 in ids
    assert p2 not in ids


def test_manual_product_immediately_searchable(client, conn):
    response = client.post(
        "/products",
        json={
            "name": "Mon Yaourt Nature",
            "brand": None,
            "kind": "product",
            "basis_unit": "g",
            "state": "as_sold",
            "values": {"protein": {"value": "4.5"}},
        },
    )
    assert response.status_code == 201
    pid = response.json()["product_id"]

    results = search(conn, "yaourt nature")
    assert any(r["product_id"] == pid for r in results)


def test_history_boost_orders_results(conn):
    p1 = _catalogue_product(conn, "Compote Pomme", brand=None)
    p2 = _catalogue_product(conn, "Compote Pomme", brand=None)
    revisions = _publish_all(
        conn,
        {
            p1: {Nutrient.ENERGY_KCAL: catalogue.NutrientValue.point(Decimal(50), Source.CIQUAL)},
            p2: {Nutrient.ENERGY_KCAL: catalogue.NutrientValue.point(Decimal(50), Source.CIQUAL)},
        },
    )
    _log_meal_item(conn, p2, revisions[p2])

    results = search(conn, "compote pomme")
    ids = [r["product_id"] for r in results]
    assert p1 in ids and p2 in ids
    assert ids.index(p2) < ids.index(p1)


def test_dish_defaults_cooking_additions_unanswered(client, conn):
    response = client.post(
        "/products",
        json={
            "name": "Curry maison",
            "brand": None,
            "kind": "dish",
            "basis_unit": "g",
            "state": "as_prepared",
        },
    )
    assert response.status_code == 201
    pid = response.json()["product_id"]
    row = conn.execute(
        sa.text("SELECT includes_cooking_additions FROM product WHERE id = :p"), {"p": pid}
    ).scalar_one()
    assert row == "unanswered"


def test_create_dish_with_yes_stores_yes(client, conn):
    response = client.post(
        "/products",
        json={
            "name": "Ragout maison",
            "brand": None,
            "kind": "dish",
            "basis_unit": "g",
            "state": "as_prepared",
            "includes_cooking_additions": "yes",
        },
    )
    assert response.status_code == 201
    pid = response.json()["product_id"]
    row = conn.execute(
        sa.text("SELECT includes_cooking_additions FROM product WHERE id = :p"), {"p": pid}
    ).scalar_one()
    assert row == "yes"


def test_create_non_dish_with_cooking_additions_422(client):
    response = client.post(
        "/products",
        json={
            "name": "Yaourt",
            "brand": None,
            "kind": "product",
            "basis_unit": "g",
            "state": "as_sold",
            "includes_cooking_additions": "yes",
        },
    )
    assert response.status_code == 422


def test_get_product_includes_owner_kind_and_cooking_additions(client):
    create = client.post(
        "/products",
        json={
            "name": "Curry maison 2",
            "brand": None,
            "kind": "dish",
            "basis_unit": "g",
            "state": "as_prepared",
        },
    )
    pid = create.json()["product_id"]

    detail = client.get(f"/products/{pid}").json()
    assert detail["owner"] == "user"
    assert detail["kind"] == "dish"
    assert detail["includes_cooking_additions"] == "unanswered"


def test_put_cooking_additions_on_catalogue_product_422(client, conn):
    pid = _catalogue_product(conn, "Compote Fraise", brand=None)
    response = client.put(f"/products/{pid}/cooking-additions", json={"value": "yes"})
    assert response.status_code == 422


def test_put_cooking_additions_on_user_dish_updates_it(client, conn):
    create = client.post(
        "/products",
        json={
            "name": "Soupe maison",
            "brand": None,
            "kind": "dish",
            "basis_unit": "g",
            "state": "as_prepared",
        },
    )
    pid = create.json()["product_id"]

    response = client.put(f"/products/{pid}/cooking-additions", json={"value": "yes"})
    assert response.status_code == 200
    assert response.json() == {"product_id": pid, "includes_cooking_additions": "yes"}

    row = conn.execute(
        sa.text("SELECT includes_cooking_additions FROM product WHERE id = :p"), {"p": pid}
    ).scalar_one()
    assert row == "yes"


def test_put_cooking_additions_updates_day_view_salt_undeclared(engine):
    """Answering the declaration later updates past days: the day view reads it live."""
    from app.api import routes_meals

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(oid="owner")

    def _conn_override():
        with engine.begin() as c:
            yield c

    app.dependency_overrides[get_connection] = _conn_override
    app.dependency_overrides[routes_meals.get_db_engine] = lambda: engine
    local_client = TestClient(app)

    try:
        create = local_client.post(
            "/products",
            json={
                "name": "Plat du jour",
                "brand": None,
                "kind": "dish",
                "basis_unit": "g",
                "state": "as_prepared",
                "values": {"salt": {"value": "1"}},
            },
        )
        assert create.status_code == 201
        pid = create.json()["product_id"]

        meal = local_client.post("/meals", json={"day": "2026-10-13", "meal_type": "lunch"})
        assert meal.status_code == 201
        meal_id = meal.json()["meal_id"]

        add = local_client.post(
            f"/meals/{meal_id}/items",
            json={
                "product_id": pid,
                "amount": "100",
                "unit": "g",
                "quantity_source": "weighed",
            },
            headers={"Idempotency-Key": "k1"},
        )
        assert add.status_code == 201, add.text

        day_before = local_client.get("/days/2026-10-13").json()
        assert "salt" in day_before["nutrients"]
        assert day_before["nutrients"]["salt"]["additions_undeclared"] == ["Plat du jour"]

        put = local_client.put(f"/products/{pid}/cooking-additions", json={"value": "yes"})
        assert put.status_code == 200

        day_after = local_client.get("/days/2026-10-13").json()
        assert day_after["nutrients"]["salt"]["additions_undeclared"] == []
    finally:
        with engine.begin() as c:
            c.execute(
                sa.text(
                    "TRUNCATE meal_item, meal, day, idempotency_key, product_state, "
                    "nutrient_value, profile_revision, source_record, product "
                    "RESTART IDENTITY CASCADE"
                )
            )


def test_normalize_strips_accents_and_collapses_whitespace():
    assert normalize("  Pêche   Sugarfree  ") == "peche sugarfree"


# --- Remaining routes (Task 13 integration follow-up) ----------------------------------------


def test_get_product_404_for_unknown_product(client):
    response = client.get("/products/999999")
    assert response.status_code == 404


def test_get_product_happy_path(client, conn):
    create = client.post(
        "/products",
        json={
            "name": "Fromage blanc",
            "brand": None,
            "kind": "product",
            "basis_unit": "g",
            "state": "as_sold",
            "values": {"protein": {"value": "7"}, "fat": {"lower": "0", "upper": "0.5"}},
        },
    )
    assert create.status_code == 201
    pid = create.json()["product_id"]

    response = client.get(f"/products/{pid}")
    assert response.status_code == 200
    body = response.json()
    assert body["product_id"] == pid
    assert body["name"] == "Fromage blanc"
    assert body["values"]["protein"] == {
        "known": True,
        "lower": "7",
        "upper": "7",
        "qualifier": "none",
        "source": "manual",
        "derivation": "direct",
        "verification": "unverified",
    }
    assert body["values"]["caffeine"]["known"] is False
    assert body["values"]["caffeine"]["lower"] is None
    assert body["portions"] == []
    assert body["pending_reviews"] == []
    assert body["overrides"] == []


def test_accept_revision_happy_path(client, conn):
    create = client.post(
        "/products",
        json={
            "name": "Yaourt nature 2",
            "brand": None,
            "kind": "product",
            "basis_unit": "g",
            "state": "as_sold",
            "values": {"protein": {"value": "4"}},
        },
    )
    pid = create.json()["product_id"]
    first_revision_id = catalogue.active_values(conn, pid)[0]

    # A >20% change on an already-adopted product goes to REVIEW: a pending revision
    # the user must explicitly accept.
    outcome = catalogue.add_source_revision(
        conn, pid, Source.MANUAL, "manual", {}, {Nutrient.PROTEIN: catalogue.NutrientValue.point(Decimal(10), Source.MANUAL)}
    )
    assert outcome.decision.action is Action.REVIEW
    pending_id = outcome.pending_revision_id
    assert pending_id is not None and pending_id != first_revision_id

    response = client.post(f"/products/{pid}/revisions/{pending_id}/accept")
    assert response.status_code == 200
    body = response.json()
    assert body == {"product_id": pid, "revision_id": pending_id, "status": "adopted"}
    assert catalogue.active_values(conn, pid)[0] == pending_id


def test_override_create_and_reconfirm(client, conn):
    create = client.post(
        "/products",
        json={
            "name": "Beurre",
            "brand": None,
            "kind": "product",
            "basis_unit": "g",
            "state": "as_sold",
            "values": {"fat": {"value": "80"}},
        },
    )
    pid = create.json()["product_id"]

    response = client.post(
        f"/products/{pid}/overrides",
        json={
            "nutrient": "fat",
            "value": {"value": "82"},
            "reason": "label correction",
            "retain_across_revisions": False,
        },
    )
    assert response.status_code == 201
    override_id = response.json()["override_id"]

    detail = client.get(f"/products/{pid}").json()
    (override,) = [o for o in detail["overrides"] if o["id"] == override_id]
    assert override["needs_reconfirm"] is False
    assert override["value"]["lower"] == "82"

    # A new adopted revision moves "active", so the override (pinned to the old
    # revision, not retained) now needs reconfirmation.
    catalogue.add_source_revision(
        conn, pid, Source.MANUAL, "manual", {}, {Nutrient.FAT: catalogue.NutrientValue.point(Decimal("80.5"), Source.MANUAL)}
    )
    detail = client.get(f"/products/{pid}").json()
    (override,) = [o for o in detail["overrides"] if o["id"] == override_id]
    assert override["needs_reconfirm"] is True

    reconfirm = client.post(f"/overrides/{override_id}/reconfirm")
    assert reconfirm.status_code == 200
    active_id = catalogue.active_values(conn, pid)[0]
    assert reconfirm.json() == {"override_id": override_id, "pinned_revision_id": active_id}

    detail = client.get(f"/products/{pid}").json()
    (override,) = [o for o in detail["overrides"] if o["id"] == override_id]
    assert override["needs_reconfirm"] is False


@respx.mock
def test_off_search_route(client):
    respx.get(
        "https://world.openfoodfacts.org/cgi/search.pl",
        params={
            "search_terms": "peche",
            "json": "1",
            "page_size": "20",
            "tagtype_0": "countries",
            "tag_contains_0": "contains",
            "tag_0": "france",
        },
    ).respond(200, json={"products": [{"code": "123", "product_name": "Peche"}]})

    response = client.get("/off/search", params={"q": "peche"})
    assert response.status_code == 200
    assert response.json() == [{"code": "123", "product_name": "Peche"}]


@respx.mock
def test_import_off_happy_path(client, conn):
    respx.get("https://world.openfoodfacts.org/api/v2/product/123.json").respond(
        200,
        json={
            "status": 1,
            "product": {
                "code": "123",
                "product_name": "Red Bull",
                "quantity": "250 ml",
                "nutriments": {"sugars_100g": 11},
            },
        },
    )

    response = client.post("/products/import-off/123")
    assert response.status_code == 201
    pid = response.json()["product_id"]
    assert catalogue.active_values(conn, pid)[1][Nutrient.SUGARS].known


@respx.mock
def test_import_off_needs_basis_without_basis_param(client):
    respx.get("https://world.openfoodfacts.org/api/v2/product/456.json").respond(
        200,
        json={"status": 1, "product": {"code": "456", "nutriments": {"sugars_100g": 11}}},
    )

    response = client.post("/products/import-off/456")
    assert response.status_code == 409
    # F5: every error body carries "detail"; extra machine-readable keys sit beside it.
    assert response.json() == {"detail": "needs basis", "needs_basis": True}


# --- Final fixes: F1 accept, F2 exact decimals, F3 validation ---------------------------------


def _user_product_with_pending(client, conn) -> tuple[int, int, int]:
    create = client.post(
        "/products",
        json={"name": "Skyr", "brand": None, "kind": "product", "basis_unit": "g",
              "state": "as_sold", "values": {"protein": {"value": "10"}}},
    )
    pid = create.json()["product_id"]
    first = catalogue.active_values(conn, pid)[0]
    outcome = catalogue.add_source_revision(
        conn, pid, Source.MANUAL, "manual", {},
        {Nutrient.PROTEIN: catalogue.NutrientValue.point(Decimal(20), Source.MANUAL)},
    )
    assert outcome.decision.action is Action.REVIEW
    return pid, first, outcome.pending_revision_id


def test_accept_on_catalogue_product_rejected(client, conn):
    pid = _catalogue_product(conn, "Compote Poire", brand=None)
    first = _publish_all(
        conn, {pid: {Nutrient.ENERGY_KCAL: catalogue.NutrientValue.point(Decimal(50), Source.CIQUAL)}}
    )[pid]
    outcome = catalogue.add_source_revision(
        conn, pid, Source.CIQUAL, "x", {},
        {Nutrient.ENERGY_KCAL: catalogue.NutrientValue.point(Decimal(90), Source.CIQUAL)},
    )
    pending = outcome.pending_revision_id
    assert pending is not None

    response = client.post(f"/products/{pid}/revisions/{pending}/accept")
    assert response.status_code == 409
    assert set(response.json()) == {"detail"}
    user_rows = conn.execute(
        sa.text("SELECT count(*) FROM product_state WHERE product_id = :p AND release_id IS NULL"),
        {"p": pid},
    ).scalar_one()
    assert user_rows == 0
    status = conn.execute(
        sa.text("SELECT status FROM profile_revision WHERE id = :r"), {"r": pending}
    ).scalar_one()
    assert status == "pending_review"
    assert catalogue.active_values(conn, pid)[0] == first


def test_accept_rejected_revision_refused(client, conn):
    pid, first, pending = _user_product_with_pending(client, conn)
    conn.execute(sa.text("UPDATE profile_revision SET status = 'rejected' WHERE id = :r"), {"r": pending})

    response = client.post(f"/products/{pid}/revisions/{pending}/accept")
    assert response.status_code == 409
    assert catalogue.active_values(conn, pid)[0] == first


def test_accept_user_revision_adopted_live_and_status_preserved(client, conn):
    pid, _first, pending = _user_product_with_pending(client, conn)
    conn.execute(
        sa.text("UPDATE product_state SET status = 'retired' WHERE product_id = :p AND release_id IS NULL"),
        {"p": pid},
    )

    response = client.post(f"/products/{pid}/revisions/{pending}/accept")
    assert response.status_code == 200
    row = conn.execute(
        sa.text(
            "SELECT s.status, s.active_revision_id, r.status AS rstatus FROM product_state s "
            "JOIN profile_revision r ON r.id = s.active_revision_id "
            "WHERE s.product_id = :p AND s.release_id IS NULL"
        ),
        {"p": pid},
    ).one()
    assert row.active_revision_id == pending
    assert row.rstatus == "adopted"
    assert row.status == "retired"


def test_create_product_json_number_round_trips_exactly(client):
    create = client.post(
        "/products",
        content='{"name": "Big", "kind": "product", "basis_unit": "g", '
        '"values": {"protein": {"value": 12345678901234567.1}}}',
        headers={"Content-Type": "application/json"},
    )
    assert create.status_code == 201, create.text
    body = client.get(f"/products/{create.json()['product_id']}").json()
    assert body["values"]["protein"]["lower"] == "12345678901234567.1"


def test_override_json_number_round_trips_exactly(client):
    pid = client.post(
        "/products", json={"name": "Huile", "kind": "product", "basis_unit": "g",
                           "values": {"fat": {"value": "99"}}},
    ).json()["product_id"]
    r = client.post(
        f"/products/{pid}/overrides",
        content='{"nutrient": "fat", "value": {"value": 99.123456789012345678}}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 201, r.text
    (o,) = client.get(f"/products/{pid}").json()["overrides"]
    assert o["value"]["lower"] == "99.123456789012345678"


@pytest.mark.parametrize(
    "body",
    [
        {"value": {"value": "1"}},  # missing nutrient
        {"nutrient": "vitamin_q", "value": {"value": "1"}},  # unknown nutrient
        {"nutrient": "fat", "value": {"value": "NaN"}},
        {"nutrient": "fat", "value": {"value": "Infinity"}},
        {"nutrient": "fat", "value": {"value": "-1"}},
        {"nutrient": "fat", "value": {"lower": "-1", "upper": "2"}},
        {"nutrient": "fat", "value": {"lower": "3", "upper": "2"}},
    ],
)
def test_override_invalid_input_422(client, body):
    pid = client.post(
        "/products", json={"name": "Lait", "kind": "product", "basis_unit": "ml",
                           "values": {"fat": {"value": "3"}}},
    ).json()["product_id"]
    r = client.post(f"/products/{pid}/overrides", json=body)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "values",
    [
        {"vitamin_q": {"value": "1"}},
        {"fat": {"value": "NaN"}},
        {"fat": {"value": "-2"}},
        {"fat": {"lower": "Infinity"}},
    ],
)
def test_create_product_invalid_values_422(client, values):
    r = client.post(
        "/products", json={"name": "X", "kind": "product", "basis_unit": "g", "values": values}
    )
    assert r.status_code == 422, r.text


def test_create_product_negative_density_422(client):
    r = client.post(
        "/products",
        json={"name": "X", "kind": "product", "basis_unit": "g", "density_g_per_ml": "-1"},
    )
    assert r.status_code == 422, r.text


@respx.mock
def test_import_off_bad_nutriments_become_unknown(client, conn):
    respx.get("https://world.openfoodfacts.org/api/v2/product/789.json").respond(
        200,
        json={
            "status": 1,
            "product": {
                "code": "789",
                "product_name": "Bad data",
                "quantity": "330 ml",
                "nutriments": {"sugars_100g": -1, "fat_100g": "abc", "proteins_100g": 1.5},
            },
        },
    )

    response = client.post("/products/import-off/789")
    assert response.status_code == 201, response.text
    values = catalogue.active_values(conn, response.json()["product_id"])[1]
    assert not values[Nutrient.SUGARS].known
    assert not values[Nutrient.FAT].known
    assert values[Nutrient.PROTEIN].range.lower == Decimal("1.5")


def _publish_one(conn, name: str, brand: str | None, name_en: str | None) -> int:
    pid = _catalogue_product(conn, name, brand)
    _publish_all(
        conn, {pid: {Nutrient.ENERGY_KCAL: catalogue.NutrientValue.point(Decimal(100), Source.CIQUAL)}}
    )
    if name_en:
        conn.execute(
            sa.text("UPDATE product SET name_en = :en, search_text = :st WHERE id = :p"),
            {"en": name_en, "st": search_text_for(name, brand, [name_en]), "p": pid},
        )
    return pid


def _meal_template(conn, name: str, name_en: str | None) -> int:
    return conn.execute(
        sa.text(
            "INSERT INTO meal_template (name, name_en, cuisine, chain, estimate) "
            "VALUES (:n, :en, 'fr', NULL, true) RETURNING id"
        ),
        {"n": name, "en": name_en},
    ).scalar_one()


def test_search_returns_name_en(conn):
    _publish_one(conn, "Poulet rôti", None, "Roast chicken")
    _publish_one(conn, "Pomme", None, None)
    by_name = {r["name"]: r for r in search(conn, "poulet")}
    assert by_name["Poulet rôti"]["name_en"] == "Roast chicken"
    assert by_name["Poulet rôti"]["type"] == "product"
    assert search(conn, "pomme")[0]["name_en"] is None


def test_search_mixes_products_and_meals(conn):
    _publish_one(conn, "Couscous graine", None, "Couscous grain")
    tid = _meal_template(conn, "Couscous royal", "Royal couscous")
    results = search(conn, "couscous")
    assert [r["type"] for r in results] == ["product", "meal"]
    meal = results[1]
    assert meal == {
        "type": "meal",
        "template_id": tid,
        "name": "Couscous royal",
        "name_en": "Royal couscous",
        "cuisine": "fr",
        "chain": None,
        "estimate": True,
    }


def test_short_search_stays_empty_when_meals_exist(conn):
    _meal_template(conn, "Aubergine bake", None)
    assert search(conn, "a") == []


def test_meal_search_is_capped_at_five(conn):
    for index in range(6):
        _meal_template(conn, f"Curry bowl {index}", None)

    results = search(conn, "curry")
    assert len([result for result in results if result["type"] == "meal"]) == 5


def test_meal_search_matches_names_not_cuisine_or_chain(conn):
    template_id = _meal_template(conn, "Rice and lentils", None)
    conn.execute(
        sa.text("UPDATE meal_template SET cuisine = 'Chicken dishes', chain = 'Chicken Street' WHERE id = :id"),
        {"id": template_id},
    )

    assert search(conn, "rice")[0]["template_id"] == template_id
    assert search(conn, "chicken") == []


def test_search_english_case_and_accents(conn):
    _publish_one(conn, "Crème brûlée", None, "Creme Brulee")
    _meal_template(conn, "Gratin dauphinois", "Potato Gratin Dauphinoise")
    assert [r["type"] for r in search(conn, "CREME BRULEE")] == ["product"]
    assert [r["type"] for r in search(conn, "potato gratin")] == ["meal"]
    assert [r["type"] for r in search(conn, "DAUPHINOÏSE")] == ["meal"]
    _publish_one(conn, "Œuf, cru", None, "Egg, raw")
    assert [r["name"] for r in search(conn, "oeuf")] == ["Œuf, cru"]
    assert [r["name"] for r in search(conn, "Œuf")] == ["Œuf, cru"]
    assert [r["name"] for r in search(conn, "EGG ")] == ["Œuf, cru"]


def test_normalize_folds_ligatures():
    from app.services.search import normalize

    assert normalize("Œuf") == normalize("oeuf") == "oeuf"
    assert normalize("Æ") == "ae"
