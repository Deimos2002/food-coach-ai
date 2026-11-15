"""Weights, profile, targets, saved meals and explicit aliases against real Postgres.

A fresh database is created per test module and migrated with ``alembic upgrade head``.
Authentication is overridden to a fixed ``CurrentUser``. Follows the fixture pattern of
``tests/integration/test_logging_api.py`` and ``test_catalogue_db.py``.
"""

from __future__ import annotations

import os
import threading
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.api import routes_body, routes_meals, routes_saved
from app.api.auth import CurrentUser, get_current_user
from app.config import Settings
from app.db import catalogue
from app.db.engine import make_engine
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.domain.values import NutrientValue, Source
from app.main import create_app

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]

TRUNCATE_SQL = (
    "TRUNCATE weight_entry, profile, alias, saved_meal_item, saved_meal, "
    "meal_item, meal, day, idempotency_key, product_state, "
    "nutrient_override, nutrient_value, profile_revision, source_record, "
    "product, active_release, catalogue_release, user_account "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture(scope="module")
def engine():
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"test_body_saved_{uuid.uuid4().hex[:12]}"
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
    app.dependency_overrides[routes_body.get_db_engine] = lambda: engine
    app.dependency_overrides[routes_saved.get_db_engine] = lambda: engine
    app.dependency_overrides[routes_meals.get_db_engine] = lambda: engine
    try:
        yield TestClient(app)
    finally:
        with engine.begin() as c:
            c.execute(sa.text(TRUNCATE_SQL))


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


def _retire(engine, pid: int, successors: list[int] | None = None) -> None:
    successors = successors or []
    with engine.begin() as c:
        release = c.execute(sa.text("SELECT release_id FROM active_release")).scalar_one()
        rev = c.execute(
            sa.text("SELECT active_revision_id FROM product_state WHERE product_id = :p"),
            {"p": pid},
        ).scalar_one()
        catalogue.set_product_state(
            c, release, pid, ProductState(Status.RETIRED, successors=tuple(successors)), rev
        )


def _meal(client, day: str = "2026-10-12", meal_type: str = "lunch") -> int:
    r = client.post("/meals", json={"day": day, "meal_type": meal_type})
    assert r.status_code == 201, r.text
    return r.json()["meal_id"]


def test_review_focus_second_weight_same_day_needs_replace(client):
    assert client.put("/weights/2026-10-12", json={"kg": "72.0"}).status_code == 200
    r = client.put("/weights/2026-10-12", json={"kg": "72.5"})
    assert r.status_code == 409
    r2 = client.put("/weights/2026-10-12", json={"kg": "72.5"}, params={"replace": "true"})
    assert r2.status_code == 200
    assert r2.json()["kg"] == "72.5"


def test_weight_out_of_range_422(client):
    assert client.put("/weights/2026-10-12", json={"kg": "-50"}).status_code == 422
    assert client.put("/weights/2026-10-12", json={"kg": "500"}).status_code == 422


def test_profile_height_out_of_range_422(client):
    profile_body = {
        "sex": "male",
        "birth_year": 1996,
        "height_cm": "-1",
        "activity": "1.55",
        "goal": "lose",
        "pace_pct_per_week": "0.5",
    }
    assert client.put("/profile", json=profile_body).status_code == 422


def test_profile_activity_must_be_a_known_level_422(client):
    profile_body = {
        "sex": "male",
        "birth_year": 1996,
        "height_cm": "180",
        "activity": "1.6",
        "goal": "lose",
        "pace_pct_per_week": "0.5",
    }
    assert client.put("/profile", json=profile_body).status_code == 422


def test_profile_pace_above_cap_422(client):
    profile_body = {
        "sex": "male",
        "birth_year": 1996,
        "height_cm": "180",
        "activity": "1.55",
        "goal": "lose",
        "pace_pct_per_week": "1.5",
    }
    assert client.put("/profile", json=profile_body).status_code == 422


def test_profile_birth_year_too_recent_422(client):
    profile_body = {
        "sex": "male",
        "birth_year": 2024,
        "height_cm": "180",
        "activity": "1.55",
        "goal": "lose",
        "pace_pct_per_week": "0.5",
    }
    assert client.put("/profile", json=profile_body).status_code == 422


def test_concurrent_first_time_put_both_succeed(client):
    # Race-safe insert (ON CONFLICT DO NOTHING RETURNING, not check-then-insert): with
    # replace=true both the insert-winner and the insert-loser (who falls through to an
    # UPDATE) succeed, with no crash and no deadlock, however the race resolves.
    barrier = threading.Barrier(2)
    results: list = [None, None]

    def worker(i: int) -> None:
        barrier.wait()
        results[i] = client.put(
            "/weights/2026-11-01", json={"kg": "70"}, params={"replace": "true"}
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r.status_code for r in results] == [200, 200]


def test_second_put_same_day_without_replace_409(client):
    assert client.put("/weights/2026-11-02", json={"kg": "70"}).status_code == 200
    r = client.put("/weights/2026-11-02", json={"kg": "71"})
    assert r.status_code == 409


def test_a19_edit_typo_recomputes_trend(client):
    r = client.put("/weights/2026-10-01", json={"kg": "7.5"})
    assert r.status_code == 200
    r2 = client.put(
        "/weights/2026-10-01", json={"kg": "72.5"}, params={"replace": "true"}
    )
    assert r2.status_code == 200
    trend = client.get("/weights/trend").json()
    assert len(trend) == 1
    assert trend[0]["trend"] == "72.50"


def test_targets_endpoint_matches_domain_example(client):
    profile_body = {
        "sex": "male",
        "birth_year": 1996,
        "height_cm": "180",
        "activity": "1.55",
        "goal": "lose",
        "pace_pct_per_week": "0.5",
    }
    assert client.put("/profile", json=profile_body).status_code == 200
    assert client.put("/weights/2026-10-12", json={"kg": "80"}).status_code == 200
    r = client.get("/targets", params={"day": "2026-10-12"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kcal"] == 2319
    assert body["protein_g"] == 144


def test_a12_saved_meal_use_blocks_retired_adds_others(client, engine):
    kept = _catalogue_product(engine, "Pomme", Unit.G, {})
    retired = _catalogue_product(engine, "Vieux Produit", Unit.G, {})
    _retire(engine, retired, successors=[kept])

    r = client.post(
        "/saved-meals",
        json={
            "name": "Mon repas",
            "items": [
                {"product_id": kept, "amount": "100", "unit": "g"},
                {"product_id": retired, "amount": "50", "unit": "g"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    saved_id = r.json()["saved_meal_id"]

    meal_id = _meal(client)
    r2 = client.post(f"/saved-meals/{saved_id}/use", json={"meal_id": meal_id})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert len(body["added"]) == 1
    assert len(body["blocked"]) == 1
    assert body["blocked"][0]["product_id"] == retired
    assert body["blocked"][0]["reason"] == "retired"
    assert body["blocked"][0]["successors"] == [kept]


def test_use_saved_meal_twice_with_different_idempotency_keys_adds_twice(client, engine):
    product_id = _catalogue_product(engine, "Riz", Unit.G, {})
    r = client.post(
        "/saved-meals",
        json={"name": "Riz simple", "items": [{"product_id": product_id, "amount": "100", "unit": "g"}]},
    )
    assert r.status_code == 201, r.text
    saved_id = r.json()["saved_meal_id"]
    meal_id = _meal(client, day="2026-10-13")

    r1 = client.post(
        f"/saved-meals/{saved_id}/use",
        json={"meal_id": meal_id},
        headers={"Idempotency-Key": "use-1"},
    )
    r2 = client.post(
        f"/saved-meals/{saved_id}/use",
        json={"meal_id": meal_id},
        headers={"Idempotency-Key": "use-2"},
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert len(r1.json()["added"]) == 1
    assert len(r2.json()["added"]) == 1
    assert r1.json()["added"] != r2.json()["added"]


def test_use_saved_meal_same_idempotency_key_twice_adds_once(client, engine):
    product_id = _catalogue_product(engine, "Pain", Unit.G, {})
    r = client.post(
        "/saved-meals",
        json={"name": "Pain simple", "items": [{"product_id": product_id, "amount": "50", "unit": "g"}]},
    )
    assert r.status_code == 201, r.text
    saved_id = r.json()["saved_meal_id"]
    meal_id = _meal(client, day="2026-10-14")

    r1 = client.post(
        f"/saved-meals/{saved_id}/use",
        json={"meal_id": meal_id},
        headers={"Idempotency-Key": "replay-key"},
    )
    r2 = client.post(
        f"/saved-meals/{saved_id}/use",
        json={"meal_id": meal_id},
        headers={"Idempotency-Key": "replay-key"},
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json() == r2.json()


def test_use_saved_meal_without_header_keeps_old_dedup_behaviour(client, engine):
    product_id = _catalogue_product(engine, "Oeuf", Unit.G, {})
    r = client.post(
        "/saved-meals",
        json={"name": "Oeuf simple", "items": [{"product_id": product_id, "amount": "60", "unit": "g"}]},
    )
    assert r.status_code == 201, r.text
    saved_id = r.json()["saved_meal_id"]
    meal_id = _meal(client, day="2026-10-15")

    r1 = client.post(f"/saved-meals/{saved_id}/use", json={"meal_id": meal_id})
    r2 = client.post(f"/saved-meals/{saved_id}/use", json={"meal_id": meal_id})
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json() == r2.json()


def test_alias_suspended_when_target_split(client, engine):
    kept = _catalogue_product(engine, "Yaourt", Unit.G, {})
    split = _catalogue_product(engine, "Mix de produits", Unit.G, {})

    r = client.post("/aliases", json={"text": "yop", "product_id": kept})
    assert r.status_code == 201, r.text
    r2 = client.post("/aliases", json={"text": "melange", "product_id": split})
    assert r2.status_code == 201, r2.text

    _retire(engine, split, successors=[kept])

    aliases = {a["text"]: a for a in client.get("/aliases").json()}
    assert aliases["yop"]["suspended"] is False
    assert aliases["melange"]["suspended"] is True
