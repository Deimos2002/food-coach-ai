"""Open Food Facts lookup and import into user-owned products."""

import json
import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation

import httpx
import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.db import catalogue
from app.db.tables import portion
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.domain.values import Derivation, Nutrient, NutrientValue, Range, Source

OFF_FIELDS: dict[Nutrient, str] = dict(
    zip(
        Nutrient,
        (
            "energy-kcal_100g",
            "proteins_100g",
            "carbohydrates_100g",
            "sugars_100g",
            "fat_100g",
            "saturated-fat_100g",
            "fiber_100g",
            "salt_100g",
            "caffeine_100g",
            "alcohol_100g",
            "polyols_100g",
            "erythritol_100g",
        ),
        strict=True,
    )
)
_BASE_URL = "https://world.openfoodfacts.org"
_HEADERS = {"User-Agent": "FoodCoach/0.1 (personal)"}


class NeedsBasis(Exception):
    """The source does not identify a mass or volume basis."""


def detect_basis(quantity: str | None) -> Unit | None:
    if not quantity:
        return None
    match = re.search(r"\d\s*(ml|cl|l|kg|g)\b", quantity, re.IGNORECASE)
    if match is None:
        return None
    return Unit.ML if match.group(1).lower() in {"ml", "cl", "l"} else Unit.G


async def fetch_off_product(client: httpx.AsyncClient, barcode: str) -> dict | None:
    response = await client.get(f"{_BASE_URL}/api/v2/product/{barcode}.json", headers=_HEADERS)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    body = json.loads(response.text, parse_float=Decimal)
    return None if body.get("status") == 0 else body.get("product")


async def search_off(client: httpx.AsyncClient, query: str, page_size: int = 20) -> list[dict]:
    response = await client.get(
        f"{_BASE_URL}/cgi/search.pl",
        headers=_HEADERS,
        params={
            "search_terms": query,
            "json": 1,
            "page_size": page_size,
            "tagtype_0": "countries",
            "tag_contains_0": "contains",
            "tag_0": "france",
        },
    )
    response.raise_for_status()
    return json.loads(response.text, parse_float=Decimal).get("products", [])


def _off_value(raw: object) -> NutrientValue:
    """One OFF nutriment as a point; missing, malformed, non-finite or negative is unknown."""
    if raw is None or isinstance(raw, bool):
        return NutrientValue.unknown(Source.OFF_CONTRIBUTOR)
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
        return NutrientValue.point(value, Source.OFF_CONTRIBUTOR)
    except (ValueError, InvalidOperation):
        return NutrientValue.unknown(Source.OFF_CONTRIBUTOR)


def off_values(nutriments: dict) -> dict[Nutrient, NutrientValue]:
    values = {nutrient: _off_value(nutriments.get(field)) for nutrient, field in OFF_FIELDS.items()}
    caffeine = values[Nutrient.CAFFEINE]
    if caffeine.known:
        values[Nutrient.CAFFEINE] = replace(
            caffeine, range=Range.point(caffeine.range.lower * Decimal(1000)),
            derivation=Derivation.CONVERTED,
        )
    # OFF alcohol is % vol, not grams; only zero converts without assuming a density.
    alcohol = values[Nutrient.ALCOHOL]
    if not alcohol.known or alcohol.range.lower != 0:
        values[Nutrient.ALCOHOL] = NutrientValue.unknown(Source.OFF_CONTRIBUTOR)
    return values


def import_off_product(
    conn: Connection,
    product_json: dict,
    basis_override: Unit | None,
    data_generation: int,
) -> int:
    basis = basis_override or detect_basis(product_json.get("quantity"))
    if basis is None:
        raise NeedsBasis()
    nutriments = product_json.get("nutriments") or {}
    values = off_values(nutriments)
    # A savepoint keeps a failed import atomic even inside a caller's larger transaction.
    with conn.begin_nested():
        pid = catalogue.insert_product(
            conn,
            owner="user",
            kind="product",
            # Prefer the generic name, then the French name, then fall back to the barcode.
            name=product_json.get("product_name")
            or product_json.get("product_name_fr")
            or str(product_json.get("code", "Unknown product")),
            brand=product_json.get("brands"),
            basis_unit=basis,
            state="as_sold",
            barcode=product_json.get("code"),
            data_generation=data_generation,
        )
        outcome = catalogue.add_source_revision(
            conn,
            pid,
            Source.OFF_CONTRIBUTOR,
            str(product_json.get("code", "")),
            product_json,
            values,
        )
        if outcome.live_revision_id is not None:
            catalogue.set_product_state(
                conn,
                None,
                pid,
                ProductState(Status.ACTIVE),
                outcome.live_revision_id,
            )
        amount = product_json.get("product_quantity")
        if amount is not None:
            conn.execute(
                sa.insert(portion).values(
                    product_id=pid,
                    label="package",
                    amount=Decimal(str(amount)),
                    kind="package",
                )
            )
    return pid
