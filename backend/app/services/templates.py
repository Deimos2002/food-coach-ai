"""Meal template detail: per-item nutrients and totals from the live catalogue values."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.db import catalogue
from app.db.tables import meal_template, meal_template_item, product
from app.domain.day_state import DayItem, day_nutrient_state
from app.domain.item_calc import ItemNutrient, compute_item_nutrients
from app.domain.quantity import Quantity, QuantityKind, QuantitySource, Unit
from app.domain.values import Nutrient


def _item_json(n: ItemNutrient | None) -> dict[str, Any]:
    if n is None or not n.known or n.range is None:
        return {"known": False, "lower": None, "upper": None}
    return {"known": True, "lower": n.range.lower, "upper": n.range.upper}


def get_template(conn: Connection, template_id: int) -> dict[str, Any] | None:
    head = conn.execute(
        sa.select(meal_template).where(meal_template.c.id == template_id)
    ).mappings().one_or_none()
    if head is None:
        return None
    rows = conn.execute(
        sa.select(
            meal_template_item.c.product_id,
            meal_template_item.c.grams,
            product.c.name,
            sa.column("name_en"),
            product.c.basis_unit,
            product.c.density_g_per_ml,
        )
        .select_from(meal_template_item.join(product, product.c.id == meal_template_item.c.product_id))
        .where(meal_template_item.c.template_id == template_id)
        .order_by(meal_template_item.c.position)
    ).mappings().all()

    items, day_items = [], []
    for r in rows:
        grams = r["grams"]
        q = Quantity(
            QuantityKind.RECORDED, grams, grams, grams, Unit.G, QuantitySource.WEIGHED
        )
        try:
            _, values = catalogue.active_values(conn, r["product_id"])
        except LookupError:
            values = {}  # no live revision: every nutrient is unknown
        nutrients = compute_item_nutrients(
            q, Unit(r["basis_unit"]), r["density_g_per_ml"], values
        )
        day_items.append(DayItem(r["name"], nutrients))
        items.append({
            "product_id": r["product_id"],
            "name": r["name"],
            "name_en": r["name_en"],
            "grams": grams,
            "nutrients": {n.value: _item_json(nutrients.get(n)) for n in Nutrient},
        })

    totals = {}
    for n in Nutrient:
        state = day_nutrient_state(day_items, n, True)
        # Same rule as the day: any unknown ingredient makes the total unknown.
        totals[n.value] = (
            {"known": False, "lower": None, "upper": None}
            if state.unresolved
            else {"known": True, "lower": state.recorded.lower, "upper": state.recorded.upper}
        )
    return {
        **{k: head[k] for k in ("id", "name", "name_en", "cuisine", "chain", "estimate", "source_url")},
        "items": items,
        "totals": totals,
    }
