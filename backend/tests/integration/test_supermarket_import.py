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
from app.db.tables import portion, product, profile_revision, source_record
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.domain.values import Nutrient, NutrientValue, Source
from app.importers.off import import_off_product
from app.importers.supermarket import import_supermarket
from app.services.search import search

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"test_supermarket_{uuid.uuid4().hex[:12]}"
    admin = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = make_url(url).set(database=name).render_as_string(hide_password=False)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    engine = make_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def conn(engine):
    with engine.connect() as conn, conn.begin() as transaction:
        yield conn
        transaction.rollback()


def drink(**changes):
    return {
        "code": "5060517889852", "product_name": "Mango Loco", "brands": "Monster Energy",
        "quantity": "500 ml", "product_quantity": "500", "countries_tags": "en:france",
        "energy-kcal_100g": "46", "sugars_100g": "11.123456789012345678",
        "fat_100g": "0", "caffeine_100g": "", **changes,
    }


def test_import_is_searchable_catalogue_with_decimal_values_and_package(conn):
    report = import_supermarket(conn, [drink()])
    assert report.imported == 1
    hit = search(conn, "Monster")[0]
    row = conn.execute(sa.select(product).where(product.c.id == hit["product_id"])).mappings().one()
    assert (row["owner"], row["basis_unit"], row["data_generation"]) == ("catalogue", "ml", None)
    _, values = catalogue.active_values(conn, row["id"])
    assert values[Nutrient.SUGARS].range.lower == Decimal("11.123456789012345678")
    assert values[Nutrient.FAT].range.lower == Decimal(0)
    assert not values[Nutrient.CAFFEINE].known
    assert values[Nutrient.SUGARS].source == Source.OFF_CONTRIBUTOR
    package = conn.execute(sa.select(portion).where(portion.c.product_id == row["id"])).mappings().one()
    assert (package["label"], package["amount"], package["kind"]) == ("package", Decimal(500), "package")


def test_reimport_is_idempotent_carries_other_products_and_keeps_user_lineage_separate(conn):
    user_id = import_off_product(conn, {
        "code": drink()["code"], "product_name": "My drink", "quantity": "500 ml",
    }, None, 1)
    import_supermarket(conn, [drink(code="1234567890123", product_name="Orange juice", brands="Joker")])
    import_supermarket(conn, [drink()])
    pid = conn.execute(sa.select(product.c.id).where(
        product.c.owner == "catalogue", product.c.barcode == drink()["code"],
    )).scalar_one()
    import_supermarket(conn, [drink()])
    assert conn.execute(sa.select(sa.func.count()).select_from(product)).scalar_one() == 3
    assert conn.execute(sa.select(sa.func.count()).select_from(source_record).where(
        source_record.c.product_id == pid,
    )).scalar_one() == 1
    assert conn.execute(sa.select(sa.func.count()).select_from(portion).where(
        portion.c.product_id == pid,
    )).scalar_one() == 1
    assert search(conn, "Joker")
    assert catalogue.active_values(conn, user_id)


@pytest.mark.parametrize("changes", [
    {"quantity": "one can"}, {"countries_tags": "en:belgium"},
    {"code": ""}, {"product_name": " "},
    {"data_quality_errors_tags": "en:energy-value-in-kcal-does-not-match-value-in-kj"},
    {"product_quantity": "-500"}, {"product_quantity": "NaN"},
    {"main_category_en": "Laundry-detergent"}, {"main_category_en": "Cat-food"},
])
def test_incomplete_or_invalid_rows_are_reported_without_creating_products(conn, changes):
    report = import_supermarket(conn, [drink(**changes)])
    assert report.imported == 0
    assert len(report.skipped) == 1
    assert conn.execute(sa.select(sa.func.count()).select_from(product)).scalar_one() == 0


def test_refresh_never_changes_an_existing_barcodes_basis(conn):
    import_supermarket(conn, [drink()])
    report = import_supermarket(conn, [drink(quantity="500 g")])
    assert report.imported == 0
    assert "basis" in report.skipped[0]
    assert search(conn, "Monster")[0]["basis_unit"] == "ml"


def test_english_category_search_finds_french_named_fries(conn):
    import_supermarket(conn, [drink(
        code="1234567890123", product_name="Frites au four", brands="McCain",
        quantity="750 g", product_quantity="750", main_category_en="Frozen French fries",
    )])
    assert any(hit["brand"] == "McCain" for hit in search(conn, "fries"))


def test_nutrition_conflicts_wait_for_review_and_retired_products_stay_retired(conn):
    import_supermarket(conn, [drink()])
    pid = search(conn, "Monster")[0]["product_id"]
    original_revision, _ = catalogue.active_values(conn, pid)
    import_supermarket(conn, [drink(sugars_100g="0")])
    assert catalogue.active_values(conn, pid)[0] == original_revision
    assert conn.execute(sa.select(sa.func.count()).select_from(profile_revision).where(
        profile_revision.c.product_id == pid, profile_revision.c.status == "pending_review",
    )).scalar_one() == 1
    release = catalogue.create_release(conn, "retire fixture")
    catalogue.set_product_state(conn, release, pid, ProductState(Status.RETIRED), original_revision)
    catalogue.activate_release(conn, release)
    import_supermarket(conn, [drink()])
    assert not search(conn, "Monster")
    assert catalogue.load_states(conn)[pid].status == Status.RETIRED


def test_search_distinguishes_grocery_brands_from_curated_restaurant_items(conn):
    import_supermarket(conn, [drink()])
    assert search(conn, "Monster")[0]["is_fast_food"] is False
    release = catalogue.create_release(conn, "restaurant fixture")
    pid = catalogue.insert_product(
        conn, owner="catalogue", kind="product", name="Big Mac", brand="McDonald's",
        basis_unit=Unit.G, state="as_sold",
    )
    values = {n: NutrientValue.unknown(Source.LABEL) for n in Nutrient}
    values[Nutrient.ENERGY_KCAL] = NutrientValue.point(Decimal(250), Source.LABEL)
    outcome = catalogue.add_source_revision(
        conn, pid, Source.LABEL, "McDonald's:Big Mac", {"chain": "McDonald's"}, values,
    )
    catalogue.set_product_state(conn, release, pid, ProductState(Status.ACTIVE), outcome.live_revision_id)
    catalogue.activate_release(conn, release)
    assert search(conn, "Big Mac")[0]["is_fast_food"] is True


def test_unchanged_refresh_does_not_make_database_roundtrips_for_every_product(conn):
    rows = [drink(code=str(1000000000000 + i)) for i in range(30)]
    import_supermarket(conn, rows)
    statements = []

    def record(*args):
        statements.append(args[2])

    sa.event.listen(conn, "before_cursor_execute", record)
    try:
        report = import_supermarket(conn, rows)
    finally:
        sa.event.remove(conn, "before_cursor_execute", record)
    assert report.imported == 30
    assert len(statements) < 20, "Refreshing unchanged catalogue rows must use batched reads"


def test_same_nutrition_with_corrected_package_amount_updates_the_portion(conn):
    import_supermarket(conn, [drink()])
    import_supermarket(conn, [drink(quantity="250 ml", product_quantity="250")])
    assert conn.execute(sa.select(portion.c.amount)).scalar_one() == Decimal(250)
