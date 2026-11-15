"""Search and product endpoints (Task 13).

Every route is protected by ``get_current_user``. Decimals always serialize as strings
(unknown nutrients serialize as ``null``, never ``0``), via a manual JSON response built
with ``app.db.engine.json_dumps`` rather than FastAPI's default encoder (which would
turn a Decimal into a float).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Any, Literal

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.engine import Connection

from app.api.auth import CurrentUser, get_current_user
from app.db import catalogue
from app.db.engine import get_engine, json_dumps
from app.db.tables import (
    nutrient_override,
    portion,
    product,
    product_state,
    profile_revision,
)
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.domain.revisions import Override, split_overrides
from app.domain.values import (
    Derivation,
    Nutrient,
    NutrientValue,
    Qualifier,
    Range,
    Source,
    Verification,
)
from app.importers.off import NeedsBasis, fetch_off_product, import_off_product, search_off
from app.services.privacy import require_generation
from app.services.search import search as run_search

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_connection() -> Any:
    engine = get_engine()
    with engine.begin() as conn:
        yield conn


ConnDep = Annotated[Connection, Depends(get_connection)]
UserDep = Annotated[CurrentUser, Depends(get_current_user)]


def _json(content: Any, status_code: int = 200) -> Response:
    return Response(content=json_dumps(content), status_code=status_code, media_type="application/json")


# --- Request bodies ----------------------------------------------------------------------------
#
# Bodies are parsed with ``parse_float=Decimal`` so a JSON number never passes through a
# float, then validated by these models. Values must be finite and non-negative; unknown
# nutrients and malformed values are 422.

_NonNegative = Field(default=None, ge=0, allow_inf_nan=False)


class ValueIn(BaseModel):
    """A per-100 value: a point ``{"value": v}`` or a range ``{"lower": l, "upper": u|null}``."""

    model_config = ConfigDict(extra="forbid")

    value: Decimal | None = _NonNegative
    lower: Decimal | None = _NonNegative
    upper: Decimal | None = _NonNegative

    @model_validator(mode="after")
    def _shape(self) -> ValueIn:
        if self.value is not None:
            if self.lower is not None or self.upper is not None:
                raise ValueError("give either value or lower/upper, not both")
        elif self.lower is None:
            raise ValueError("a value needs 'value' or 'lower'")
        elif self.upper is not None and self.upper < self.lower:
            raise ValueError("upper is below lower")
        return self

    def to_value(self) -> NutrientValue:
        if self.value is not None:
            return NutrientValue.point(self.value, Source.MANUAL)
        assert self.lower is not None
        return NutrientValue(True, Range(self.lower, self.upper), Qualifier.NONE, Source.MANUAL)


class ProductIn(BaseModel):
    kind: Literal["product", "dish"]
    name: str = Field(min_length=1)
    brand: str | None = None
    basis_unit: Unit
    state: str = "as_sold"
    density_g_per_ml: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    density_source: str | None = None
    values: dict[Nutrient, ValueIn | None] | None = None
    includes_cooking_additions: Literal["yes", "no", "unanswered"] | None = None

    @model_validator(mode="after")
    def _only_dishes_declare(self) -> ProductIn:
        if self.kind != "dish" and self.includes_cooking_additions is not None:
            raise ValueError("only dishes declare cooking additions")
        return self


class CookingAdditionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Literal["yes", "no", "unanswered"]


class OverrideIn(BaseModel):
    nutrient: Nutrient
    value: ValueIn | None = None
    retain_across_revisions: bool = False
    reason: str = ""


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a valid JSON number")


async def decimal_json(request: Request) -> Any:
    """The request body as JSON, every non-integer number parsed as Decimal (never float)."""
    try:
        return json.loads(await request.body(), parse_float=Decimal, parse_constant=_reject_constant)
    except ValueError as exc:
        raise RequestValidationError(
            [{"type": "json_invalid", "loc": ("body",), "msg": str(exc), "input": None}]
        ) from exc


RawBody = Annotated[Any, Depends(decimal_json)]


def _validate[M: BaseModel](model: type[M], raw: Any) -> M:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise RequestValidationError([{**e, "loc": ("body", *e["loc"])} for e in errors]) from exc


def _values_from_body(raw: dict[Nutrient, ValueIn | None] | None) -> dict[Nutrient, NutrientValue]:
    raw = raw or {}
    out: dict[Nutrient, NutrientValue] = {}
    for nutrient in Nutrient:
        entry = raw.get(nutrient)
        out[nutrient] = NutrientValue.unknown(Source.MANUAL) if entry is None else entry.to_value()
    return out


def _value_to_json(value: NutrientValue) -> dict[str, Any]:
    if not value.known or value.range is None:
        return {"known": False, "lower": None, "upper": None, "qualifier": value.qualifier.value}
    return {
        "known": True,
        "lower": value.range.lower,
        "upper": value.range.upper,
        "qualifier": value.qualifier.value,
        "source": value.source.value,
        "derivation": value.derivation.value,
        "verification": value.verification.value,
    }


# --- Search ----------------------------------------------------------------------------------


@router.get("/search")
def search_endpoint(q: str = "", conn: ConnDep = None, _: UserDep = None) -> Response:
    results = run_search(conn, q)
    return _json(results)


@router.get("/off/search")
async def off_search_endpoint(q: str = "", _: UserDep = None) -> Response:
    async with httpx.AsyncClient() as client:
        products = await search_off(client, q)
    return _json(products)


# --- Products ----------------------------------------------------------------------------------


@router.post("/products", status_code=201)
def create_product(raw: RawBody, conn: ConnDep = None, user: UserDep = None) -> Response:
    body = _validate(ProductIn, raw)
    generation = require_generation(conn, user.oid)
    pid = catalogue.insert_product(
        conn,
        owner="user",
        kind=body.kind,
        name=body.name,
        brand=body.brand,
        basis_unit=body.basis_unit,
        state=body.state,
        data_generation=generation,
        includes_cooking_additions=body.includes_cooking_additions,
    )
    if body.density_g_per_ml is not None:
        conn.execute(
            sa.update(product)
            .where(product.c.id == pid)
            .values(density_g_per_ml=body.density_g_per_ml, density_source=body.density_source)
        )
    values = _values_from_body(body.values)
    outcome = catalogue.add_source_revision(conn, pid, Source.MANUAL, "manual", {}, values)
    if outcome.live_revision_id is not None:
        catalogue.set_product_state(conn, None, pid, ProductState(Status.ACTIVE), outcome.live_revision_id)
    return _json({"product_id": pid}, status_code=201)


@router.post("/products/import-off/{barcode}")
async def import_off_endpoint(
    barcode: str, basis: str | None = None, conn: ConnDep = None, user: UserDep = None
) -> Response:
    async with httpx.AsyncClient() as client:
        off_product = await fetch_off_product(client, barcode)
    if off_product is None:
        raise HTTPException(status_code=404, detail="not found")
    generation = require_generation(conn, user.oid)
    basis_override = Unit(basis) if basis else None
    try:
        pid = import_off_product(conn, off_product, basis_override, generation)
    except NeedsBasis:
        return _json({"detail": "needs basis", "needs_basis": True}, status_code=409)
    return _json({"product_id": pid}, status_code=201)


@router.get("/products/{product_id}")
def get_product(product_id: int, conn: ConnDep = None, _: UserDep = None) -> Response:
    row = conn.execute(sa.select(product).where(product.c.id == product_id)).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        active_id, values = catalogue.active_values(conn, product_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="no active revision") from exc

    portions = [
        dict(r)
        for r in conn.execute(
            sa.select(portion.c.label, portion.c.amount, portion.c.kind)
            .where(portion.c.product_id == product_id)
            .order_by(portion.c.id)
        ).mappings()
    ]
    revisions = [
        {"id": r["id"], "status": r["status"], "created_at": r["created_at"].isoformat()}
        for r in conn.execute(
            sa.select(profile_revision.c.id, profile_revision.c.status, profile_revision.c.created_at)
            .where(profile_revision.c.product_id == product_id)
            .order_by(profile_revision.c.id)
        ).mappings()
    ]
    pending_reviews = [r["id"] for r in revisions if r["status"] == "pending_review"]

    override_rows = conn.execute(
        sa.select(nutrient_override).where(nutrient_override.c.product_id == product_id)
    ).mappings()
    overrides = [
        Override(
            id=r["id"],
            version=r["version"],
            nutrient=Nutrient(r["nutrient"]),
            value=catalogue.row_to_value(r),
            pinned_revision_id=r["pinned_revision_id"],
            retain_across_revisions=r["retain_across_revisions"],
        )
        for r in override_rows
    ]
    _applied, needs_reconfirm = split_overrides(overrides, active_id)
    needs_reconfirm_ids = {o.id for o in needs_reconfirm}

    return _json(
        {
            "product_id": product_id,
            "name": row["name"],
            "brand": row["brand"],
            "basis_unit": row["basis_unit"],
            "kind": row["kind"],
            "owner": row["owner"],
            "includes_cooking_additions": row["includes_cooking_additions"],
            "values": {n.value: _value_to_json(v) for n, v in values.items()},
            "portions": portions,
            "revisions": revisions,
            "pending_reviews": pending_reviews,
            "overrides": [
                {
                    "id": o.id,
                    "version": o.version,
                    "nutrient": o.nutrient.value,
                    "value": _value_to_json(o.value),
                    "pinned_revision_id": o.pinned_revision_id,
                    "retain_across_revisions": o.retain_across_revisions,
                    "needs_reconfirm": o.id in needs_reconfirm_ids,
                }
                for o in overrides
            ],
        }
    )


@router.post("/products/{product_id}/revisions/{revision_id}/accept")
def accept_revision(product_id: int, revision_id: int, conn: ConnDep = None) -> Response:
    """Accept a pending revision of a **user-owned** product.

    Catalogue revisions only move through releases (A.6): a user state row on a catalogue
    product would override every later release and rollback, so that is 409. Only a
    ``pending_review`` revision can be accepted (a rejected one never goes live), and the
    product's existing state (status, merge, successors) is kept.
    """
    owner = conn.execute(
        sa.select(product.c.owner).where(product.c.id == product_id).with_for_update()
    ).scalar_one_or_none()
    status = conn.execute(
        sa.select(profile_revision.c.status).where(
            profile_revision.c.id == revision_id, profile_revision.c.product_id == product_id
        )
    ).scalar_one_or_none()
    if owner is None or status is None:
        raise HTTPException(status_code=404, detail="not found")
    if owner != "user":
        raise HTTPException(status_code=409, detail="catalogue revisions change only through releases")
    if status != "pending_review":
        raise HTTPException(status_code=409, detail=f"revision is {status}, not pending_review")
    current = conn.execute(
        sa.select(product_state.c.status, product_state.c.merged_into, product_state.c.successors).where(
            product_state.c.product_id == product_id, product_state.c.release_id.is_(None)
        )
    ).one_or_none()
    state = (
        ProductState(Status.ACTIVE)
        if current is None
        else ProductState(Status(current.status), current.merged_into, tuple(current.successors or ()))
    )
    conn.execute(
        sa.update(profile_revision).where(profile_revision.c.id == revision_id).values(status="adopted")
    )
    catalogue.set_product_state(conn, None, product_id, state, revision_id)
    return _json({"product_id": product_id, "revision_id": revision_id, "status": "adopted"})


@router.post("/products/{product_id}/overrides", status_code=201)
def create_override(product_id: int, raw: RawBody, conn: ConnDep = None, user: UserDep = None) -> Response:
    body = _validate(OverrideIn, raw)
    try:
        active_id, _values = catalogue.active_values(conn, product_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="no active revision") from exc
    generation = require_generation(conn, user.oid)
    nutrient = body.nutrient
    value = NutrientValue.unknown(Source.MANUAL) if body.value is None else body.value.to_value()
    value = NutrientValue(
        value.known, value.range, value.qualifier, Source.MANUAL, Derivation.DIRECT, Verification.USER_VERIFIED
    )
    next_version = (
        conn.execute(
            sa.select(sa.func.coalesce(sa.func.max(nutrient_override.c.version), 0))
            .where(nutrient_override.c.product_id == product_id, nutrient_override.c.nutrient == nutrient.value)
        ).scalar_one()
        + 1
    )
    override_id = conn.execute(
        sa.insert(nutrient_override)
        .values(
            version=next_version,
            product_id=product_id,
            nutrient=nutrient.value,
            pinned_revision_id=active_id,
            retain_across_revisions=body.retain_across_revisions,
            reason=body.reason,
            data_generation=generation,
            **catalogue.value_to_row(value),
        )
        .returning(nutrient_override.c.id)
    ).scalar_one()
    return _json({"override_id": override_id}, status_code=201)


@router.put("/products/{product_id}/cooking-additions")
def set_cooking_additions(
    product_id: int, raw: RawBody, conn: ConnDep = None, _: UserDep = None
) -> Response:
    body = _validate(CookingAdditionsIn, raw)
    row = conn.execute(
        sa.select(product.c.owner, product.c.kind).where(product.c.id == product_id)
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if row["owner"] != "user" or row["kind"] != "dish":
        raise HTTPException(status_code=422, detail="only user-owned dishes declare cooking additions")
    conn.execute(
        sa.update(product)
        .where(product.c.id == product_id)
        .values(includes_cooking_additions=body.value)
    )
    return _json({"product_id": product_id, "includes_cooking_additions": body.value})


@router.post("/overrides/{override_id}/reconfirm")
def reconfirm_override(override_id: int, conn: ConnDep = None, _: UserDep = None) -> Response:
    row = conn.execute(
        sa.select(nutrient_override.c.product_id).where(nutrient_override.c.id == override_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    active_id, _values = catalogue.active_values(conn, row.product_id)
    conn.execute(
        sa.update(nutrient_override)
        .where(nutrient_override.c.id == override_id)
        .values(pinned_revision_id=active_id)
    )
    return _json({"override_id": override_id, "pinned_revision_id": active_id})
