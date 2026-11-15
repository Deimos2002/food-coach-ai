"""Meal logging: meals, idempotent item logging with frozen snapshots, day status.

Rules (spec §5.2, §7.1, §7.2 and Addendum A):
- An item's nutrients are computed once, at log time, from the product's live revision
  with its applicable overrides, and stored on the item with ``profile_revision_id`` and
  the applied ``override_ids`` (A.5). Later revisions never change a logged item; edits
  recompute from the item's pinned revision and overrides.
- ``requested_product_id`` is what the client asked for; ``resolved_product_id`` is where
  merges led (A.6). A retired product cannot be logged (422 with its successors).
- The amount must be in the basis unit or convertible through a sourced density (A.4),
  otherwise 422. Unknown values stay unknown (never 0).
- Add-item is idempotent: the key row and the item are written in one transaction; a
  concurrent duplicate waits on the key's unique index and then returns the stored
  response; the same key with a different payload is 409. Keys expire after 7 days.
- Day status follows ``next_status``. Every user row carries ``data_generation``.
- The meal's day is the client's local date, as sent; the server never derives it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from app.db import catalogue
from app.db.tables import active_release, nutrient_override, nutrient_value, product
from app.domain.day_state import DayItem, day_nutrient_state
from app.domain.day_status import DayEvent, DayStatus, next_status
from app.domain.identity import resolve
from app.domain.item_calc import ItemNutrient, compute_item_nutrients
from app.domain.quantity import Quantity, QuantityKind, QuantitySource, Unit, UnitMismatch
from app.domain.revisions import Override, split_overrides
from app.domain.values import Nutrient, NutrientValue, Qualifier, Range

MAX_AMOUNT = Decimal(5000)
IDEMPOTENCY_TTL_DAYS = 7
COOKING_ADDITION_NUTRIENTS = frozenset({Nutrient.SALT, Nutrient.FAT})

# Query mirrors of migration 0002 (the migration is the source of truth for DDL). Kept in
# their own MetaData: ``app.db.tables.metadata`` mirrors migration 0001 only.
metadata = sa.MetaData()
meal = sa.Table(
    "meal",
    metadata,
    sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
    sa.Column("day", sa.Date, nullable=False),
    sa.Column("meal_type", sa.Text, nullable=False),
    sa.Column("skipped", sa.Boolean, nullable=False),
    sa.Column("data_generation", sa.Integer, nullable=False),
)
meal_item = sa.Table(
    "meal_item",
    metadata,
    sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
    sa.Column("meal_id", sa.Integer, nullable=False),
    sa.Column("requested_product_id", sa.Integer, nullable=False),
    sa.Column("resolved_product_id", sa.Integer, nullable=False),
    sa.Column("release_id", sa.Integer, nullable=True),
    sa.Column("profile_revision_id", sa.Integer, nullable=False),
    sa.Column("override_ids", ARRAY(sa.Integer), nullable=False),
    sa.Column("qty_kind", sa.Text, nullable=False),
    sa.Column("qty_value", sa.Numeric(asdecimal=True), nullable=False),
    sa.Column("qty_lower", sa.Numeric(asdecimal=True), nullable=False),
    sa.Column("qty_upper", sa.Numeric(asdecimal=True), nullable=False),
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column("qty_source", sa.Text, nullable=False),
    sa.Column("nutrients", JSONB, nullable=False),
    sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("dish", sa.Text, nullable=True),
    sa.Column("data_generation", sa.Integer, nullable=False),
)
day_table = sa.Table(
    "day",
    metadata,
    sa.Column("day", sa.Date, primary_key=True),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("data_generation", sa.Integer, nullable=False),
)
idempotency_key = sa.Table(
    "idempotency_key",
    metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("request_hash", sa.Text, nullable=False),
    sa.Column("response", JSONB, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)


# --- Errors --------------------------------------------------------------------------------


class LoggingError(Exception):
    """An error with an HTTP status and a JSON body, raised before anything is committed."""

    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        super().__init__(body)
        self.status_code = status_code
        self.body = body


def _err(status_code: int, detail: str, **extra: Any) -> LoggingError:
    return LoggingError(status_code, {"detail": detail, **extra})


# --- Input models --------------------------------------------------------------------------

_Amount = Field(gt=0, le=MAX_AMOUNT)
_Bound = Field(default=None, ge=0, le=MAX_AMOUNT)


class AddItemIn(BaseModel):
    product_id: int
    amount: Decimal = _Amount
    lower: Decimal | None = _Bound
    upper: Decimal | None = _Bound
    unit: Unit
    quantity_source: QuantitySource
    consumed_at: datetime | None = None
    dish: str | None = Field(default=None, max_length=200)


class PatchItemIn(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0, le=MAX_AMOUNT)
    lower: Decimal | None = _Bound
    upper: Decimal | None = _Bound
    unit: Unit | None = None
    quantity_source: QuantitySource | None = None
    consumed_at: datetime | None = None


MealType = Literal["breakfast", "lunch", "dinner", "snack"]


# --- Serialisation -------------------------------------------------------------------------


def dec(d: Decimal | None) -> str | None:
    """Plain decimal text (no exponent, no trailing zeros); JSON carries Decimals as text."""
    if d is None:
        return None
    normalized = d.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _nutrients_to_json(values: Mapping[Nutrient, ItemNutrient]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for nutrient in sorted(values, key=lambda n: n.value):
        v = values[nutrient]
        out[nutrient.value] = {
            "known": v.known,
            "lower": None if v.range is None else dec(v.range.lower),
            "upper": None if v.range is None else dec(v.range.upper),
            "qualifier": v.qualifier.value,
        }
    return out


def _nutrients_from_json(data: Mapping[str, Any]) -> dict[Nutrient, ItemNutrient]:
    out: dict[Nutrient, ItemNutrient] = {}
    for key, v in data.items():
        rng = None
        if v["known"]:
            upper = v["upper"]
            rng = Range(Decimal(str(v["lower"])), None if upper is None else Decimal(str(upper)))
        out[Nutrient(key)] = ItemNutrient(bool(v["known"]), rng, Qualifier(v["qualifier"]))
    return out


def nutrients_from_json(data: Mapping[str, Any]) -> dict[Nutrient, ItemNutrient]:
    """Public reader for a stored ``meal_item.nutrients`` snapshot."""
    return _nutrients_from_json(data)


def _request_hash(meal_id: int, payload: AddItemIn) -> str:
    canonical = {
        "meal_id": meal_id,
        "product_id": payload.product_id,
        "amount": dec(payload.amount),
        "lower": dec(payload.lower),
        "upper": dec(payload.upper),
        "unit": payload.unit.value,
        "quantity_source": payload.quantity_source.value,
        "consumed_at": None if payload.consumed_at is None else payload.consumed_at.isoformat(),
        "dish": payload.dish,
    }
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Quantities and nutrients --------------------------------------------------------------


def _quantity(
    amount: Decimal,
    lower: Decimal | None,
    upper: Decimal | None,
    unit: Unit,
    source: QuantitySource,
) -> Quantity:
    """A point when no range is given, otherwise an estimation range around ``amount``."""
    try:
        if lower is None and upper is None:
            return Quantity.recorded(amount, unit, source)
        if lower is None or upper is None:
            raise ValueError("an estimation range needs both lower and upper")
        return Quantity.estimated(amount, lower, upper, unit, source)
    except (TypeError, ValueError) as exc:
        raise _err(422, str(exc)) from exc


@dataclass(frozen=True)
class _ProductRow:
    id: int
    owner: str
    kind: str
    name: str
    basis_unit: Unit
    density: Decimal | None
    includes_cooking_additions: str | None


def _product_row(conn: Connection, product_id: int) -> _ProductRow | None:
    row = conn.execute(sa.select(product).where(product.c.id == product_id)).mappings().first()
    if row is None:
        return None
    return _ProductRow(
        id=row["id"],
        owner=row["owner"],
        kind=row["kind"],
        name=row["name"],
        basis_unit=Unit(row["basis_unit"]),
        density=row["density_g_per_ml"],
        includes_cooking_additions=row["includes_cooking_additions"],
    )


def _override_from_row(row: Mapping[str, Any]) -> Override:
    return Override(
        id=row["id"],
        version=row["version"],
        nutrient=Nutrient(row["nutrient"]),
        value=catalogue.row_to_value(row),
        pinned_revision_id=row["pinned_revision_id"],
        retain_across_revisions=row["retain_across_revisions"],
    )


def _latest_overrides(conn: Connection, product_id: int, generation: int) -> list[Override]:
    """The latest version of each nutrient's override for the product."""
    rows = conn.execute(
        sa.select(nutrient_override)
        .where(
            nutrient_override.c.product_id == product_id,
            nutrient_override.c.data_generation == generation,
        )
        .order_by(nutrient_override.c.nutrient, nutrient_override.c.version.desc())
    ).mappings()
    latest: dict[str, Override] = {}
    for row in rows:
        latest.setdefault(row["nutrient"], _override_from_row(row))
    return list(latest.values())


def _overrides_by_id(conn: Connection, ids: list[int]) -> list[Override]:
    if not ids:
        return []
    rows = conn.execute(sa.select(nutrient_override).where(nutrient_override.c.id.in_(ids)))
    return [_override_from_row(r) for r in rows.mappings()]


def _revision_values(conn: Connection, revision_id: int) -> dict[Nutrient, NutrientValue]:
    rows = conn.execute(
        sa.select(nutrient_value).where(nutrient_value.c.revision_id == revision_id)
    ).mappings()
    return {Nutrient(r["nutrient"]): catalogue.row_to_value(r) for r in rows}


def _apply(
    values: Mapping[Nutrient, NutrientValue], overrides: list[Override]
) -> dict[Nutrient, NutrientValue]:
    out = dict(values)
    for o in overrides:
        out[o.nutrient] = o.value
    return out


def _compute(prod: _ProductRow, q: Quantity, values: Mapping[Nutrient, NutrientValue]):
    try:
        return compute_item_nutrients(q, prod.basis_unit, prod.density, values)
    except UnitMismatch as exc:
        raise _err(422, str(exc)) from exc


# --- Day status ----------------------------------------------------------------------------


def _apply_day_event(
    conn: Connection, day: date, event: DayEvent, generation: int, now: datetime
) -> DayStatus:
    """Lock (creating if needed) the day row and move it with ``next_status``."""
    conn.execute(
        pg_insert(day_table)
        .values(day=day, status=DayStatus.NOT_LOGGED.value, data_generation=generation)
        .on_conflict_do_nothing(index_elements=[day_table.c.day])
    )
    row = conn.execute(
        sa.select(day_table.c.status, day_table.c.completed_at)
        .where(day_table.c.day == day)
        .with_for_update()
    ).one()
    current = DayStatus(row.status)
    new = next_status(current, event)
    completed_at = row.completed_at
    if new is DayStatus.COMPLETE:
        if event is DayEvent.MARK_COMPLETE or completed_at is None:
            completed_at = now
    else:
        completed_at = None
    conn.execute(
        sa.update(day_table)
        .where(day_table.c.day == day)
        .values(status=new.value, completed_at=completed_at, data_generation=generation)
    )
    return new


def set_day_status(
    conn: Connection, day: date, status: str, generation: int, now: datetime
) -> dict[str, Any]:
    if status == DayStatus.COMPLETE.value:
        event = DayEvent.MARK_COMPLETE
    elif status == DayStatus.PARTIAL.value:
        event = DayEvent.MARK_PARTIAL
    else:
        raise _err(422, "status must be 'complete' or 'partial'")
    new = _apply_day_event(conn, day, event, generation, now)
    return {"day": day.isoformat(), "status": new.value}


# --- Meals ---------------------------------------------------------------------------------


def create_meal(conn: Connection, day: date, meal_type: str, generation: int) -> int:
    return conn.execute(
        sa.insert(meal)
        .values(day=day, meal_type=meal_type, skipped=False, data_generation=generation)
        .returning(meal.c.id)
    ).scalar_one()


def _meal_day(conn: Connection, meal_id: int, lock: bool = False) -> date:
    stmt = sa.select(meal.c.day).where(meal.c.id == meal_id)
    if lock:
        stmt = stmt.with_for_update()
    day = conn.execute(stmt).scalar_one_or_none()
    if day is None:
        raise _err(404, "meal not found")
    return day


def set_skipped(conn: Connection, meal_id: int, skipped: bool, generation: int) -> dict:
    updated = conn.execute(
        sa.update(meal)
        .where(meal.c.id == meal_id)
        .values(skipped=skipped, data_generation=generation)
        .returning(meal.c.id)
    ).scalar_one_or_none()
    if updated is None:
        raise _err(404, "meal not found")
    return {"meal_id": meal_id, "skipped": skipped}


# --- Items ---------------------------------------------------------------------------------


def _claim_key(conn: Connection, key: str, request_hash: str) -> dict | None:
    """Insert the key row, or return the stored response of an identical earlier request.

    A concurrent insert of the same key blocks on the primary key until the first
    transaction ends; afterwards the row is either visible (return its response) or gone
    (the first one rolled back, so this insert proceeds).
    """
    conn.execute(
        sa.delete(idempotency_key).where(
            idempotency_key.c.key == key,
            idempotency_key.c.created_at
            < sa.func.now() - sa.text(f"interval '{IDEMPOTENCY_TTL_DAYS} days'"),
        )
    )
    inserted = conn.execute(
        pg_insert(idempotency_key)
        .values(key=key, request_hash=request_hash, created_at=sa.func.now())
        .on_conflict_do_nothing(index_elements=[idempotency_key.c.key])
        .returning(idempotency_key.c.key)
    ).scalar_one_or_none()
    if inserted is not None:
        return None
    row = conn.execute(
        sa.select(idempotency_key.c.request_hash, idempotency_key.c.response).where(
            idempotency_key.c.key == key
        )
    ).one()
    if row.request_hash != request_hash or row.response is None:
        raise _err(409, "idempotency key reused with a different payload")
    return row.response


def add_item(
    conn: Connection,
    meal_id: int,
    key: str,
    payload: AddItemIn,
    generation: int,
    now: datetime,
) -> dict:
    """Log one item. Must run inside a single transaction owned by the caller."""
    stored = _claim_key(conn, key, _request_hash(meal_id, payload))
    if stored is not None:
        return stored

    # A.6: resolution, the live revision and the recorded release_id must all come from
    # one release. Hold the active-release pointer FOR SHARE until commit; activation
    # (``catalogue.activate_release``, an upsert that row-locks the pointer) waits for us.
    conn.execute(sa.select(active_release.c.release_id).with_for_update(read=True))

    day = _meal_day(conn, meal_id)
    q = _quantity(
        payload.amount, payload.lower, payload.upper, payload.unit, payload.quantity_source
    )

    if _product_row(conn, payload.product_id) is None:
        raise _err(404, "product not found")
    try:
        resolution = resolve(payload.product_id, catalogue.load_states(conn))
    except ValueError as exc:
        raise _err(422, f"product cannot be resolved: {exc}") from exc
    if not resolution.ok:
        raise _err(422, "product retired", successors=list(resolution.successors))
    prod = _product_row(conn, resolution.product_id)
    if prod is None:
        raise _err(422, "product cannot be resolved")
    try:
        revision_id, values = catalogue.active_values(conn, prod.id)
    except LookupError as exc:
        raise _err(422, "product has no active revision") from exc

    applied = split_overrides(_latest_overrides(conn, prod.id, generation), revision_id)[0]
    nutrients = _nutrients_to_json(_compute(prod, q, _apply(values, applied)))
    release_id = (
        conn.execute(sa.select(active_release.c.release_id)).scalar_one_or_none()
        if prod.owner == "catalogue"
        else None
    )

    item_id = conn.execute(
        sa.insert(meal_item)
        .values(
            meal_id=meal_id,
            requested_product_id=payload.product_id,
            resolved_product_id=prod.id,
            release_id=release_id,
            profile_revision_id=revision_id,
            override_ids=sorted(o.id for o in applied),
            qty_kind=q.kind.value,
            qty_value=q.value,
            qty_lower=q.lower,
            qty_upper=q.upper,
            unit=q.unit.value,
            qty_source=q.source.value,
            nutrients=nutrients,
            logged_at=now,
            consumed_at=payload.consumed_at,
            dish=payload.dish,
            data_generation=generation,
        )
        .returning(meal_item.c.id)
    ).scalar_one()
    response = {"item_id": item_id, "nutrients": nutrients}
    conn.execute(
        sa.update(idempotency_key).where(idempotency_key.c.key == key).values(response=response)
    )
    _apply_day_event(conn, day, DayEvent.ITEM_ADDED, generation, now)
    return response


def _item(conn: Connection, item_id: int) -> Mapping[str, Any]:
    row = (
        conn.execute(sa.select(meal_item).where(meal_item.c.id == item_id).with_for_update())
        .mappings()
        .first()
    )
    if row is None:
        raise _err(404, "meal item not found")
    return row


def patch_item(
    conn: Connection, item_id: int, patch: PatchItemIn, generation: int, now: datetime
) -> dict:
    """Recompute an item with its **pinned** revision and overrides, never the live ones."""
    row = _item(conn, item_id)
    fields = patch.model_fields_set
    # Omitted quantity fields keep their stored values: an amount-only edit of an estimate
    # stays an estimate with its bounds. A point is recorded only when the item already is
    # one, or when the client explicitly sends ``"lower": null, "upper": null``. The merged
    # quantity is validated (lower <= amount <= upper) by ``_quantity``.
    point = row["qty_kind"] == QuantityKind.RECORDED.value
    amount = patch.amount if patch.amount is not None else row["qty_value"]
    lower = patch.lower if "lower" in fields else (None if point else row["qty_lower"])
    upper = patch.upper if "upper" in fields else (None if point else row["qty_upper"])
    unit = patch.unit or Unit(row["unit"])
    source = patch.quantity_source or QuantitySource(row["qty_source"])
    q = _quantity(amount, lower, upper, unit, source)

    prod = _product_row(conn, row["resolved_product_id"])
    assert prod is not None
    values = _apply(
        _revision_values(conn, row["profile_revision_id"]),
        _overrides_by_id(conn, list(row["override_ids"])),
    )
    nutrients = _nutrients_to_json(_compute(prod, q, values))
    changes: dict[str, Any] = {
        "qty_kind": q.kind.value,
        "qty_value": q.value,
        "qty_lower": q.lower,
        "qty_upper": q.upper,
        "unit": q.unit.value,
        "qty_source": q.source.value,
        "nutrients": nutrients,
        "data_generation": generation,
    }
    if "consumed_at" in fields:
        changes["consumed_at"] = patch.consumed_at
    conn.execute(sa.update(meal_item).where(meal_item.c.id == item_id).values(**changes))
    _apply_day_event(conn, _meal_day(conn, row["meal_id"]), DayEvent.ITEM_EDITED, generation, now)
    return {"item_id": item_id, "nutrients": nutrients}


def delete_item(conn: Connection, item_id: int, generation: int, now: datetime) -> None:
    row = _item(conn, item_id)
    conn.execute(sa.delete(meal_item).where(meal_item.c.id == item_id))
    _apply_day_event(conn, _meal_day(conn, row["meal_id"]), DayEvent.ITEM_DELETED, generation, now)


def move_item(
    conn: Connection, item_id: int, target_meal_id: int, generation: int, now: datetime
) -> dict:
    row = _item(conn, item_id)
    source_day = _meal_day(conn, row["meal_id"])
    target_day = _meal_day(conn, target_meal_id)
    conn.execute(
        sa.update(meal_item)
        .where(meal_item.c.id == item_id)
        .values(meal_id=target_meal_id, data_generation=generation)
    )
    if source_day != target_day:
        # Lock days in a fixed order to avoid deadlocks between opposite moves.
        events = {source_day: DayEvent.ITEM_MOVED_OUT, target_day: DayEvent.ITEM_MOVED_IN}
        for d in sorted(events):
            _apply_day_event(conn, d, events[d], generation, now)
    else:
        _apply_day_event(conn, target_day, DayEvent.ITEM_MOVED_IN, generation, now)
    return {"item_id": item_id, "meal_id": target_meal_id}


# --- Recent items ---------------------------------------------------------------------------


def recent_items(conn: Connection, limit: int) -> list[dict[str, Any]]:
    """The most recently logged distinct resolved products, newest first (Task 0: web app).

    One query: an inner ``DISTINCT ON (resolved_product_id)`` picks each product's most
    recent log row, then the outer query orders those by ``logged_at`` and caps the count.
    """
    inner = (
        sa.select(
            meal_item.c.resolved_product_id,
            meal_item.c.qty_value,
            meal_item.c.unit,
            meal_item.c.logged_at,
        )
        .distinct(meal_item.c.resolved_product_id)
        .order_by(meal_item.c.resolved_product_id, meal_item.c.logged_at.desc())
        .subquery()
    )
    stmt = (
        sa.select(
            inner.c.resolved_product_id,
            inner.c.qty_value,
            inner.c.unit,
            inner.c.logged_at,
            product.c.name,
            product.c.brand,
            product.c.basis_unit,
        )
        .join(product, product.c.id == inner.c.resolved_product_id)
        .order_by(inner.c.logged_at.desc())
        .limit(limit)
    )
    rows = conn.execute(stmt).mappings().all()
    return [
        {
            "product_id": r["resolved_product_id"],
            "name": r["name"],
            "brand": r["brand"],
            "basis_unit": r["basis_unit"],
            "last_amount": dec(r["qty_value"]),
            "last_unit": r["unit"],
            "last_logged_at": r["logged_at"].isoformat(),
        }
        for r in rows
    ]


# --- Day view ------------------------------------------------------------------------------


@contextmanager
def snapshot_connection(engine: Engine) -> Iterator[Connection]:
    """A connection in a read-only REPEATABLE READ transaction: every read sees one snapshot."""
    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ", postgresql_readonly=True
    ) as conn, conn.begin():
        yield conn


def get_day(conn: Connection, day: date) -> dict[str, Any]:
    """The day view. Call it inside ``snapshot_connection`` so all reads share a snapshot."""
    status_row = conn.execute(
        sa.select(day_table.c.status, day_table.c.completed_at).where(day_table.c.day == day)
    ).one_or_none()
    status = DayStatus.NOT_LOGGED if status_row is None else DayStatus(status_row.status)
    completed_at = None if status_row is None else status_row.completed_at

    meals = (
        conn.execute(sa.select(meal).where(meal.c.day == day).order_by(meal.c.id)).mappings().all()
    )
    items = (
        conn.execute(
            sa.select(
                meal_item,
                product.c.name,
                product.c.kind,
                product.c.owner,
                product.c.includes_cooking_additions,
            )
            .join(product, product.c.id == meal_item.c.resolved_product_id)
            .where(meal_item.c.meal_id.in_([m["id"] for m in meals]))
            .order_by(meal_item.c.id)
        )
        .mappings()
        .all()
    )

    day_items: list[DayItem] = []
    by_meal: dict[int, list[dict[str, Any]]] = {m["id"]: [] for m in meals}
    for it in items:
        # A.3: a user dish whose cooking additions are not declared "yes" is unresolved
        # for salt and fat. Read live, so answering the question later updates past days.
        undeclared = (
            it["kind"] == "dish"
            and it["owner"] == "user"
            and it["includes_cooking_additions"] != "yes"
        )
        day_items.append(
            DayItem(
                name=it["name"],
                nutrients=_nutrients_from_json(it["nutrients"]),
                unresolved_for=COOKING_ADDITION_NUTRIENTS if undeclared else frozenset(),
            )
        )
        by_meal[it["meal_id"]].append(
            {
                "item_id": it["id"],
                "name": it["name"],
                "requested_product_id": it["requested_product_id"],
                "resolved_product_id": it["resolved_product_id"],
                "profile_revision_id": it["profile_revision_id"],
                "quantity": {
                    "kind": it["qty_kind"],
                    "value": dec(it["qty_value"]),
                    "lower": dec(it["qty_lower"]),
                    "upper": dec(it["qty_upper"]),
                    "unit": it["unit"],
                    "source": it["qty_source"],
                },
                "nutrients": it["nutrients"],
                "logged_at": it["logged_at"].isoformat(),
                "consumed_at": None if it["consumed_at"] is None else it["consumed_at"].isoformat(),
                "dish": it["dish"],
            }
        )

    log_complete = status is DayStatus.COMPLETE
    nutrients: dict[str, Any] = {}
    for n in Nutrient:
        s = day_nutrient_state(day_items, n, log_complete)
        nutrients[n.value] = {
            "recorded": {"lower": dec(s.recorded.lower), "upper": dec(s.recorded.upper)},
            "unresolved": list(s.unresolved),
            "unquantified": list(s.unquantified),
            "additions_undeclared": list(s.additions_undeclared),
            "fields_complete": s.fields_complete,
            "log_complete": s.log_complete,
        }
    return {
        "day": day.isoformat(),
        "status": status.value,
        "completed_at": None if completed_at is None else completed_at.isoformat(),
        "meals": [
            {
                "meal_id": m["id"],
                "meal_type": m["meal_type"],
                "skipped": m["skipped"],
                "items": by_meal[m["id"]],
            }
            for m in meals
        ],
        "nutrients": nutrients,
    }
