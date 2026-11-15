"""Saved meals and explicit aliases (Task 14).

Saved items reference stable product ids and resolve at use time (Task 5): a saved
item whose product was retired is blocked (with its successors) rather than logged; a
merged product resolves through the chain transparently. Logging a saved meal's items
goes through Task 12's ``add_item`` so idempotency and frozen snapshots apply.

Aliases are explicit: ``POST /aliases`` is the only code path that creates one. Each
alias feeds ``product.search_text`` (Task 13) and is reported ``suspended`` when its
target product currently resolves as retired.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.db import catalogue
from app.db.tables import product
from app.domain.identity import SavedItemRef, Status, resolve
from app.domain.quantity import QuantitySource, Unit
from app.services import logging as logging_svc
from app.services.search import search_text_for

metadata = sa.MetaData()

saved_meal = sa.Table(
    "saved_meal",
    metadata,
    sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("data_generation", sa.Integer, nullable=False),
)

saved_meal_item = sa.Table(
    "saved_meal_item",
    metadata,
    sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
    sa.Column("saved_meal_id", sa.Integer, nullable=False),
    sa.Column("product_id", sa.Integer, nullable=False),
    sa.Column("amount", sa.Numeric(asdecimal=True), nullable=False),
    sa.Column("unit", sa.Text, nullable=False),
)

alias = sa.Table(
    "alias",
    metadata,
    sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("product_id", sa.Integer, nullable=False),
    sa.Column("data_generation", sa.Integer, nullable=False),
)


class SavedError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- Saved meals -----------------------------------------------------------------------


@dataclass(frozen=True)
class SavedItemIn:
    product_id: int
    amount: Decimal
    unit: Unit


def create_saved_meal(
    conn: Connection, name: str, items: list[SavedItemIn], generation: int
) -> int:
    meal_id = conn.execute(
        sa.insert(saved_meal).values(name=name, data_generation=generation).returning(
            saved_meal.c.id
        )
    ).scalar_one()
    if items:
        conn.execute(
            sa.insert(saved_meal_item),
            [
                {
                    "saved_meal_id": meal_id,
                    "product_id": it.product_id,
                    "amount": it.amount,
                    "unit": it.unit.value,
                }
                for it in items
            ],
        )
    return meal_id


def dec(d: Decimal) -> str:
    """Plain decimal text (no exponent, no trailing zeros)."""
    normalized = d.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def list_saved_meals(conn: Connection) -> list[dict[str, Any]]:
    """All saved meals with their items, ordered by name (Task 0: web saved-meal list)."""
    meals = conn.execute(
        sa.select(saved_meal.c.id, saved_meal.c.name).order_by(saved_meal.c.name)
    ).all()
    if not meals:
        return []
    meal_ids = [m.id for m in meals]
    item_rows = conn.execute(
        sa.select(
            saved_meal_item.c.saved_meal_id,
            saved_meal_item.c.product_id,
            saved_meal_item.c.amount,
            saved_meal_item.c.unit,
            product.c.name,
        )
        .join(product, product.c.id == saved_meal_item.c.product_id)
        .where(saved_meal_item.c.saved_meal_id.in_(meal_ids))
        .order_by(saved_meal_item.c.id)
    ).all()
    items_by_meal: dict[int, list[dict[str, Any]]] = {mid: [] for mid in meal_ids}
    for row in item_rows:
        items_by_meal[row.saved_meal_id].append(
            {
                "product_id": row.product_id,
                "name": row.name,
                "amount": dec(row.amount),
                "unit": row.unit,
            }
        )
    return [
        {"id": m.id, "name": m.name, "items": items_by_meal[m.id]}
        for m in meals
    ]


def _basis_of(conn: Connection) -> dict[int, Unit]:
    rows = conn.execute(sa.select(product.c.id, product.c.basis_unit)).all()
    return {r.id: Unit(r.basis_unit) for r in rows}


def use_saved_meal(
    conn: Connection,
    saved_meal_id: int,
    target_meal_id: int,
    generation: int,
    now: datetime,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    saved = conn.execute(
        sa.select(saved_meal.c.id).where(saved_meal.c.id == saved_meal_id)
    ).scalar_one_or_none()
    if saved is None:
        raise SavedError(404, "saved meal not found")
    rows = conn.execute(
        sa.select(
            saved_meal_item.c.product_id, saved_meal_item.c.amount, saved_meal_item.c.unit
        )
        .where(saved_meal_item.c.saved_meal_id == saved_meal_id)
        .order_by(saved_meal_item.c.id)
    ).all()

    states = catalogue.load_states(conn)
    basis_of = _basis_of(conn)

    added: list[int] = []
    blocked: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        ref = SavedItemRef(row.product_id, row.amount, Unit(row.unit))
        # Inline re-implementation of app.domain.identity.resolve_saved_item
        # (identity.py:66-78), not a call to it: that function reports only a string
        # ("blocked: retired" / "blocked: unit changed") with no successors, but this
        # endpoint's contract needs the successor list for a retired block. Changing
        # Task 5's file is out of scope here, so this calls the lower-level `resolve`
        # it itself wraps and applies the identical unit-mismatch rule. See
        # tests/domain/test_identity_parity.py for the parity check that keeps this
        # in sync with identity.py's rule.
        resolution = resolve(ref.product_id, states)
        if not resolution.ok:
            blocked.append(
                {
                    "product_id": ref.product_id,
                    "reason": "retired",
                    "successors": list(resolution.successors),
                }
            )
            continue
        resolved_unit = basis_of.get(resolution.product_id)
        if resolved_unit is not None and resolved_unit != ref.unit:
            blocked.append(
                {"product_id": ref.product_id, "reason": "unit changed", "successors": []}
            )
            continue
        if idempotency_key:
            key = f"saved:{idempotency_key}:{index}"
        else:
            key = f"saved:{saved_meal_id}:{target_meal_id}:{index}"
        # saved_meal_item has no quantity_source column (brief's exact schema:
        # product_id, amount, unit only), so replaying it through add_item needs one;
        # USER_ESTIMATE since a saved recipe amount is not a fresh weighing.
        payload = logging_svc.AddItemIn(
            product_id=resolution.product_id,
            amount=ref.amount,
            unit=ref.unit,
            quantity_source=QuantitySource.USER_ESTIMATE,
        )
        result = logging_svc.add_item(conn, target_meal_id, key, payload, generation, now)
        added.append(result["item_id"])

    return {"added": added, "blocked": blocked}


# --- Aliases -----------------------------------------------------------------------------


def _refresh_search_text(conn: Connection, product_id: int) -> None:
    row = conn.execute(
        sa.select(product.c.name, product.c.brand).where(product.c.id == product_id)
    ).one_or_none()
    if row is None:
        return
    alias_texts = [
        a
        for a in conn.execute(
            sa.select(alias.c.text).where(alias.c.product_id == product_id)
        ).scalars()
    ]
    conn.execute(
        sa.update(product)
        .where(product.c.id == product_id)
        .values(search_text=search_text_for(row.name, row.brand, alias_texts))
    )


def create_alias(conn: Connection, text: str, product_id: int, generation: int) -> int:
    exists = conn.execute(
        sa.select(product.c.id).where(product.c.id == product_id)
    ).scalar_one_or_none()
    if exists is None:
        raise SavedError(404, "product not found")
    alias_id = conn.execute(
        sa.insert(alias)
        .values(text=text, product_id=product_id, data_generation=generation)
        .returning(alias.c.id)
    ).scalar_one()
    _refresh_search_text(conn, product_id)
    return alias_id


def update_alias(conn: Connection, alias_id: int, text: str, product_id: int, generation: int) -> None:
    row = conn.execute(sa.select(alias.c.product_id).where(alias.c.id == alias_id)).one_or_none()
    if row is None:
        raise SavedError(404, "alias not found")
    old_product_id = row.product_id
    exists = conn.execute(
        sa.select(product.c.id).where(product.c.id == product_id)
    ).scalar_one_or_none()
    if exists is None:
        raise SavedError(404, "product not found")
    conn.execute(
        sa.update(alias)
        .where(alias.c.id == alias_id)
        .values(text=text, product_id=product_id, data_generation=generation)
    )
    _refresh_search_text(conn, old_product_id)
    if old_product_id != product_id:
        _refresh_search_text(conn, product_id)


def list_aliases(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        sa.select(alias.c.id, alias.c.text, alias.c.product_id).order_by(alias.c.id)
    ).all()
    states = catalogue.load_states(conn)
    out: list[dict[str, Any]] = []
    for r in rows:
        state = states.get(r.product_id)
        suspended = state is not None and state.status == Status.RETIRED
        out.append(
            {
                "id": r.id,
                "text": r.text,
                "product_id": r.product_id,
                "suspended": suspended,
            }
        )
    return out
