"""Weekly fact sheet builder and Week API against a real Postgres (coach Task 4, spec §2.6-2.7)."""

from __future__ import annotations

import json
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

from app.api import routes_body, routes_meals, routes_week
from app.api.auth import CurrentUser, get_current_user
from app.coach.factsheet import build_fact_sheet, llm_view
from app.config import Settings
from app.db import catalogue
from app.db.engine import make_engine
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.domain.values import Nutrient, NutrientValue, Source
from app.main import create_app

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
OID = "factsheet-owner-oid-7f3a"
WEEK = "2026-W42"  # Mon 12 Oct .. Sun 18 Oct 2026


@pytest.fixture(scope="module")
def engine():
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"test_factsheet_{uuid.uuid4().hex[:12]}"
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
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(oid=OID)
    for mod in (routes_meals, routes_body, routes_week):
        app.dependency_overrides[mod.get_db_engine] = lambda: engine
    try:
        yield TestClient(app)
    finally:
        with engine.begin() as c:
            c.execute(
                sa.text(
                    "TRUNCATE meal_item, meal, day, idempotency_key, product_state, "
                    "nutrient_override, nutrient_value, profile_revision, source_record, "
                    "product, active_release, catalogue_release, profile, weight_entry, "
                    "user_account RESTART IDENTITY CASCADE"
                )
            )
            c.execute(sa.text("UPDATE guideline_card SET verified_by_user_at = NULL"))


def _p(v: str) -> NutrientValue:
    return NutrientValue.point(Decimal(v), Source.LABEL)


def _catalogue(engine, products: list[tuple[str, str | None, Unit, dict]]) -> list[int]:
    """Create products and publish them all in one active release."""
    ids = []
    with engine.begin() as c:
        release = catalogue.create_release(c, f"release {uuid.uuid4().hex[:6]}")
        for name, brand, basis, values in products:
            pid = catalogue.insert_product(
                c, owner="catalogue", kind="product", name=name, brand=brand,
                basis_unit=basis, state="as_sold",
            )
            rev = catalogue.add_source_revision(c, pid, Source.LABEL, name, {}, values)
            catalogue.set_product_state(
                c, release, pid, ProductState(Status.ACTIVE), rev.live_revision_id
            )
            ids.append(pid)
        catalogue.activate_release(c, release)
    return ids


def _setup_body(client) -> None:
    r = client.put("/profile", json={
        "sex": "female", "birth_year": 1995, "height_cm": "165", "activity": "1.375",
        "goal": "lose", "pace_pct_per_week": "0.5",
    })
    assert r.status_code == 200, r.text
    for day, kg in (("2026-10-12", "72.6"), ("2026-10-18", "72.1")):
        r = client.put(f"/weights/{day}", json={"kg": kg})
        assert r.status_code == 200, r.text


def _meal(client, day: str) -> int:
    r = client.post("/meals", json={"day": day, "meal_type": "lunch"})
    assert r.status_code == 201, r.text
    return r.json()["meal_id"]


def _add(client, meal_id: int, pid: int, amount: str, unit: str) -> dict:
    r = client.post(
        f"/meals/{meal_id}/items",
        json={"product_id": pid, "amount": amount, "unit": unit, "quantity_source": "weighed"},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _complete(client, day: str) -> None:
    r = client.put(f"/days/{day}/status", json={"status": "complete"})
    assert r.status_code == 200, r.text


@pytest.fixture
def seeded(client, engine):
    """Red Bull Peach Edition (11 g sugars/100 ml) on three complete days, plus its
    Sugarfree variant, an unrelated product and a product with unknown sugars."""
    peach, sugarfree, cola_g, crepe = _catalogue(engine, [
        ("Red Bull Peach Edition", "Red Bull", Unit.ML,
         {Nutrient.SUGARS: _p("11"), Nutrient.ENERGY_KCAL: _p("46")}),
        ("Red Bull Peach Edition Sugarfree", "red bull", Unit.ML,
         {Nutrient.SUGARS: _p("0"), Nutrient.ENERGY_KCAL: _p("3")}),
        # Same name, but a gram basis: never a swap.
        ("Red Bull Peach Edition Zero", "Red Bull", Unit.G,
         {Nutrient.SUGARS: _p("0"), Nutrient.ENERGY_KCAL: _p("3")}),
        ("Homemade crêpes", None, Unit.G,
         {Nutrient.SUGARS: NutrientValue.unknown(Source.LABEL), Nutrient.ENERGY_KCAL: _p("200")}),
    ])
    _setup_body(client)
    items = {}
    for day in ("2026-10-12", "2026-10-13", "2026-10-14"):
        m = _meal(client, day)
        items[day] = (m, _add(client, m, peach, "750", "ml")["item_id"])
        _add(client, m, crepe, "100", "g")
        _complete(client, day)
    return {"peach": peach, "sugarfree": sugarfree, "zero_g": cola_g, "items": items}


def _sheet(engine) -> dict:
    with engine.connect() as c:
        return build_fact_sheet(c, WEEK)


def _sugar_fact(sheet: dict) -> dict:
    return next(f for f in sheet["facts"] if f["rule"] == "sugars_daily")


def test_fact_sheet_has_no_identity(seeded, engine):
    sheet = _sheet(engine)
    text = json.dumps(sheet)
    assert OID not in text
    for key in ("sex", "birth_year", "height_cm", "activity", "oid", "user"):
        assert f'"{key}"' not in text
    assert sheet["schema_version"] == 1
    assert sheet["week"] == WEEK
    assert sheet["goal"] == {"type": "lose", "pace_pct_per_week": "0.5"}
    assert set(sheet["trend"]) == {"kg_start", "kg_end"}
    assert sheet["days_included"] == ["2026-10-12", "2026-10-13", "2026-10-14"]
    assert sheet["days_missing"] == ["2026-10-15", "2026-10-16", "2026-10-17", "2026-10-18"]
    assert sheet["coverage"].startswith("3 complete days")
    # Decimals are strings, never floats.
    assert not any(isinstance(v, float) for v in _walk(sheet))


def _walk(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)
    else:
        yield obj


def test_hash_stable_and_changes_with_log(seeded, client, engine):
    a, b = _sheet(engine), _sheet(engine)
    assert a["hash"] == b["hash"] and len(a["hash"]) == 64
    m = _meal(client, "2026-10-15")
    _add(client, m, seeded["peach"], "250", "ml")
    assert _sheet(engine)["hash"] != a["hash"]


def test_contributor_and_swap_ids_resolve(seeded, engine):
    sheet = _sheet(engine)
    contributors = [c for f in sheet["facts"] for c in f.get("contributors", [])]
    swaps = [s for f in sheet["facts"] for s in f.get("swaps", [])]
    assert contributors and swaps
    c_ids = [c["id"] for c in contributors]
    s_ids = [s["id"] for s in swaps]
    assert c_ids == [f"C{i + 1}" for i in range(len(c_ids))]
    assert s_ids == [f"S{i + 1}" for i in range(len(s_ids))]
    for f in sheet["facts"]:
        own = {c["id"] for c in f.get("contributors", [])}
        assert all(s["for"] in own for s in f.get("swaps", []))


def test_swap_found_from_catalogue_variant(seeded, engine):
    fact = _sugar_fact(_sheet(engine))
    assert fact["claim"] == "exceeds"
    (top,) = [c for c in fact["contributors"] if c["name"] == "Red Bull Peach Edition"]
    assert top["share"] == ["1.00", "1.00"]
    assert fact["unknown_for"] == ["Homemade crêpes"]
    swaps = [s for s in fact["swaps"] if s["for"] == top["id"]]
    assert [s["name"] for s in swaps] == ["Red Bull Peach Edition Sugarfree"]
    assert swaps[0]["values"] == {
        "from_lower": "11", "to_upper": "0", "unit": "g/100ml", "basis": "catalogue_per_100",
    }


def test_week_endpoint_item_refs_not_in_llm_view(seeded, client):
    r = client.get(f"/week/{WEEK}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["complete_days"] == 3
    assert {c["id"] for c in body["cards"]} >= {"who_free_sugars", "who_salt"}
    fact = _sugar_fact(body["fact_sheet"])
    top = fact["contributors"][0]
    expected = [
        {"day": day, "meal_id": m, "item_id": i} for day, (m, i) in seeded["items"].items()
    ]
    assert top["item_refs"] == expected
    view = llm_view(body["fact_sheet"])
    assert "item_refs" not in json.dumps(view)
    assert "item_refs" in json.dumps(body["fact_sheet"])  # llm_view does not mutate
    assert view["hash"] == body["fact_sheet"]["hash"]


def _user_dish(engine, name: str, values) -> int:
    with engine.begin() as c:
        pid = catalogue.insert_product(
            c, owner="user", kind="dish", name=name, brand=None, basis_unit=Unit.G,
            state="as_prepared", data_generation=1, includes_cooking_additions="unanswered",
        )
        rev = catalogue.add_source_revision(c, pid, Source.MANUAL, "m", {}, values)
        catalogue.set_product_state(c, None, pid, ProductState(Status.ACTIVE), rev.live_revision_id)
    return pid


def test_undeclared_additions_dish_is_open_ended(client, engine):
    (crisps,) = _catalogue(engine, [
        ("Chips nature", None, Unit.G, {Nutrient.SALT: _p("2")}),
    ])
    curry = _user_dish(engine, "Chicken curry", {Nutrient.SALT: _p("1")})
    _setup_body(client)
    m = _meal(client, "2026-10-12")
    _add(client, m, curry, "400", "g")  # 4 g salt recorded, additions undeclared
    _add(client, m, crisps, "100", "g")  # 2 g salt
    _complete(client, "2026-10-12")

    fact = next(f for f in _sheet(engine)["facts"] if f["rule"] == "salt_daily")
    assert fact["claim"] == "exceeds"  # at least 6 g > 5 g
    assert fact["additions_undeclared"] == ["Chicken curry"]
    assert fact["unknown_for"] == []
    curry_c, chips_c = fact["contributors"]
    # Ranked by its recorded lower bound, but open-ended.
    assert curry_c["name"] == "Chicken curry" and curry_c["additions_undeclared"] is True
    assert curry_c["share"][1] is None
    assert chips_c["additions_undeclared"] is False
    # The fact's total has no upper bound, so no share has a positive lower bound.
    assert [c["share"][0] for c in fact["contributors"]] == ["0.00", "0.00"]


@pytest.mark.parametrize("week", ["2026-42", "2026-W54", "2026-W00", "abc", "2026-W4"])
def test_iso_week_validation_422(client, week):
    assert client.get(f"/week/{week}").status_code == 422


def test_swap_product_id_in_week_api_but_not_in_llm_view(seeded, client):
    from app.coach.factsheet import sheet_hash

    body = client.get(f"/week/{WEEK}").json()
    sheet = body["fact_sheet"]
    (swap,) = _sugar_fact(sheet)["swaps"]
    assert swap["product_id"] == seeded["sugarfree"]
    view = llm_view(sheet)
    assert all("product_id" not in s for f in view["facts"] for s in f["swaps"])
    assert "product_id" in json.dumps(sheet)  # llm_view does not mutate
    # Ids the LLM never sees do not change the sheet's hash.
    assert sheet_hash(view) == sheet_hash(sheet) == sheet["hash"]
