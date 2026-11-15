"""Catalogue persistence: products, source revisions, releases and product states.

Rules (spec §4 and Addendum A):
- A profile revision comes from exactly one source record; values are never stitched.
- **The release pointer is the only notion of "live"** (A.6). The live revision of a
  product is the ``active_revision_id`` of its ``product_state`` row: the user-owned row
  (``release_id IS NULL``) for user products, the active release's row for catalogue
  products. A product without such a row has no live revision.
- ``profile_revision.status`` is lifecycle only (``adopted`` / ``pending_review`` /
  ``rejected``) and is never read to decide what is live.
- A profile (same ``nutrition_hash``) is stored once per product and reused.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from app.db.tables import (
    active_release,
    catalogue_release,
    nutrient_value,
    product,
    product_state,
    profile_revision,
    source_record,
)
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.domain.revisions import Action, RevisionDecision, decide_revision, nutrition_hash
from app.domain.values import (
    Derivation,
    Nutrient,
    NutrientValue,
    Qualifier,
    Range,
    Source,
    Verification,
)

# --- Pure row mapping ----------------------------------------------------------------------


def value_to_row(value: NutrientValue) -> dict[str, Any]:
    """Columns for one nutrient value. Unknown stores no range; upper None = unbounded."""
    rng = value.range if value.known else None
    return {
        "known": value.known,
        "lower": None if rng is None else rng.lower,
        "upper": None if rng is None else rng.upper,
        "qualifier": value.qualifier.value,
        "source": value.source.value,
        "derivation": value.derivation.value,
        "verification": value.verification.value,
    }


def row_to_value(row: Mapping[str, Any]) -> NutrientValue:
    known = bool(row["known"])
    rng: Range | None = None
    if known:
        lower = row["lower"]
        upper = row["upper"]
        rng = Range(
            lower if isinstance(lower, Decimal) else Decimal(str(lower)),
            None if upper is None else upper if isinstance(upper, Decimal) else Decimal(str(upper)),
        )
    return NutrientValue(
        known=known,
        range=rng,
        qualifier=Qualifier(row["qualifier"]),
        source=Source(row["source"]),
        derivation=Derivation(row["derivation"]),
        verification=Verification(row["verification"]),
    )


def _basic_search_text(name: str, brand: str | None) -> str:
    """Normalized ``name brand`` plus the same text with spaces removed (Task 13)."""
    from app.services.search import search_text_for

    return search_text_for(name, brand)


# --- Releases ------------------------------------------------------------------------------


#: Serialises release creation (snapshot to activation) across concurrent importers.
RELEASE_LOCK_KEY = 0x464F4F44_52454C  # "FOODREL"


def create_release(conn: Connection, note: str) -> int:
    """New release, starting as a copy of the active release's product states.

    Takes a transaction-scoped advisory lock first, so concurrent imports queue until the
    holder's transaction ends and the later one snapshots the earlier one's activation.
    """
    conn.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": RELEASE_LOCK_KEY})
    release_id = conn.execute(
        sa.insert(catalogue_release).values(note=note).returning(catalogue_release.c.id)
    ).scalar_one()
    conn.execute(
        sa.text(
            "INSERT INTO product_state "
            "(release_id, product_id, status, merged_into, successors, active_revision_id) "
            "SELECT :new, product_id, status, merged_into, successors, active_revision_id "
            "FROM product_state "
            "WHERE release_id = (SELECT release_id FROM active_release WHERE singleton)"
        ),
        {"new": release_id},
    )
    return release_id


def activate_release(conn: Connection, release_id: int) -> None:
    """Point ``active_release`` at ``release_id``.

    The upsert row-locks the pointer, so it waits for any add-item transaction holding it
    FOR SHARE (``services.logging.add_item``): an item never mixes two releases (A.6).
    """
    stmt = pg_insert(active_release).values(singleton=True, release_id=release_id)
    stmt = stmt.on_conflict_do_update(
        index_elements=[active_release.c.singleton],
        set_={"release_id": stmt.excluded.release_id, "activated_at": sa.func.now()},
    )
    conn.execute(stmt)


def _active_release_id_subquery() -> sa.ScalarSelect[Any]:
    return sa.select(active_release.c.release_id).scalar_subquery()


# --- Products ------------------------------------------------------------------------------


def insert_product(
    conn: Connection,
    *,
    owner: str,
    kind: str,
    name: str,
    brand: str | None,
    basis_unit: Unit,
    state: str,
    barcode: str | None = None,
    data_generation: int | None = None,
    includes_cooking_additions: str | None = None,
) -> int:
    if owner == "user" and data_generation is None:
        raise ValueError("a user-owned product needs a data_generation")
    if owner == "catalogue" and data_generation is not None:
        raise ValueError("catalogue products carry no data_generation")
    return conn.execute(
        sa.insert(product)
        .values(
            owner=owner,
            kind=kind,
            name=name,
            brand=brand,
            basis_unit=Unit(basis_unit).value,
            state=state,
            # A.3: only user-owned dishes carry the declaration; it starts unanswered
            # unless the caller supplies a value for a user dish.
            includes_cooking_additions=(
                (includes_cooking_additions or "unanswered") if kind == "dish" and owner == "user" else None
            ),
            search_text=_basic_search_text(name, brand),
            barcode=barcode,
            data_generation=data_generation,
        )
        .returning(product.c.id)
    ).scalar_one()


# --- Revisions -----------------------------------------------------------------------------


def _live_revision_id(conn: Connection, product_id: int) -> int | None:
    """The revision named by the product's live state row, or None. No other fallback."""
    return conn.execute(
        sa.select(product_state.c.active_revision_id)
        .where(
            product_state.c.product_id == product_id,
            sa.or_(
                product_state.c.release_id.is_(None),
                product_state.c.release_id == _active_release_id_subquery(),
            ),
        )
        .order_by(product_state.c.release_id.asc().nulls_first())
        .limit(1)
    ).scalar_one_or_none()


def _load_values(conn: Connection, revision_id: int) -> dict[Nutrient, NutrientValue]:
    rows = conn.execute(
        sa.select(nutrient_value).where(nutrient_value.c.revision_id == revision_id)
    ).mappings()
    return {Nutrient(row["nutrient"]): row_to_value(row) for row in rows}


@dataclass(frozen=True)
class RevisionOutcome:
    """Result of recording a fetched source record.

    - ``decision``: the revision decision (ADOPT / REVIEW / NOOP).
    - ``live_revision_id``: the revision the caller must place in its staging release with
      ``set_product_state`` (the carry-forward): the new or reused revision on ADOPT, the
      current live revision on NOOP and REVIEW. ``None`` only when the product has no live
      revision and nothing was adopted.
    - ``pending_revision_id``: the revision awaiting the user's review on REVIEW (new,
      reused, or a previously rejected match); ``None`` otherwise. It is never placed.
    """

    decision: RevisionDecision
    live_revision_id: int | None
    pending_revision_id: int | None


def add_source_revision(
    conn: Connection,
    product_id: int,
    source: Source,
    external_id: str,
    raw: dict,
    values: Mapping[Nutrient, NutrientValue],
) -> RevisionOutcome:
    """Record a fetched source record and decide on it against the product's live revision.

    Outcomes (see ``RevisionOutcome``):
    - NOOP (same nutrition as the live revision): stores nothing; ``live`` = current live.
    - ADOPT: ``live`` = the new revision, or the existing revision with the same
      ``nutrition_hash`` (reused; a pending one becomes ``adopted``).
    - REVIEW: ``live`` = current live; ``pending`` = the new ``pending_review`` revision, or
      the reused one with the same hash. A reused ``adopted`` revision keeps its status
      (statuses are never downgraded); a reused ``pending_review`` one gets fresh reasons.
    - A match with a ``rejected`` revision never goes live: the outcome is REVIEW with that
      revision as ``pending`` and its status untouched, even if the decision was ADOPT.

    Live state: ADOPT on a **user-owned** product moves its user state row
    (``release_id IS NULL``), if one exists, to the adopted revision. On a **catalogue**
    product nothing is written to any state row: the caller (an importer) must put
    ``live_revision_id`` into its staging release with ``set_product_state`` for every
    outcome, otherwise the product disappears when that release is activated.
    """
    # Serialise revision decisions per product.
    conn.execute(sa.select(product.c.id).where(product.c.id == product_id).with_for_update()).one()

    current_id = _live_revision_id(conn, product_id)
    current = None if current_id is None else _load_values(conn, current_id)
    decision = decide_revision(current, values)
    if decision.action is Action.NOOP:
        return RevisionOutcome(decision, current_id, None)

    hash_ = nutrition_hash(values)
    existing = conn.execute(
        sa.select(profile_revision.c.id, profile_revision.c.status).where(
            profile_revision.c.product_id == product_id,
            profile_revision.c.nutrition_hash == hash_,
        )
    ).one_or_none()

    if existing is not None:
        revision_id, status = existing
        if status == "rejected":
            reasons = decision.reasons or ("matches a previously rejected revision",)
            return RevisionOutcome(
                RevisionDecision(Action.REVIEW, tuple(reasons)), current_id, revision_id
            )
        if decision.action is Action.ADOPT:
            if status != "adopted":
                conn.execute(
                    sa.update(profile_revision)
                    .where(profile_revision.c.id == revision_id)
                    .values(status="adopted", reasons=[])
                )
        elif status == "pending_review":
            conn.execute(
                sa.update(profile_revision)
                .where(profile_revision.c.id == revision_id)
                .values(reasons=list(decision.reasons))
            )
    else:
        revision_id = _insert_revision(
            conn,
            product_id,
            source,
            external_id,
            raw,
            values,
            hash_,
            status="adopted" if decision.action is Action.ADOPT else "pending_review",
            reasons=decision.reasons,
        )

    if decision.action is Action.REVIEW:
        return RevisionOutcome(decision, current_id, revision_id)

    conn.execute(
        sa.update(product_state)
        .where(
            product_state.c.product_id == product_id,
            product_state.c.release_id.is_(None),
        )
        .values(active_revision_id=revision_id)
    )
    return RevisionOutcome(decision, revision_id, None)


def _insert_revision(
    conn: Connection,
    product_id: int,
    source: Source,
    external_id: str,
    raw: dict,
    values: Mapping[Nutrient, NutrientValue],
    hash_: str,
    *,
    status: str,
    reasons: tuple[str, ...],
) -> int:
    record_id = conn.execute(
        sa.insert(source_record)
        .values(
            product_id=product_id,
            source=Source(source).value,
            external_id=external_id,
            raw=raw,
            nutrition_hash=hash_,
        )
        .returning(source_record.c.id)
    ).scalar_one()
    revision_id = conn.execute(
        sa.insert(profile_revision)
        .values(
            product_id=product_id,
            source_record_id=record_id,
            nutrition_hash=hash_,
            status=status,
            reasons=list(reasons),
        )
        .returning(profile_revision.c.id)
    ).scalar_one()
    if values:
        conn.execute(
            sa.insert(nutrient_value),
            [
                {"revision_id": revision_id, "nutrient": Nutrient(n).value, **value_to_row(v)}
                for n, v in values.items()
            ],
        )
    return revision_id


def active_values(conn: Connection, product_id: int) -> tuple[int, dict[Nutrient, NutrientValue]]:
    """The live revision (from ``product_state`` only) and its values.

    Raises ``LookupError`` when the product has no live state row, e.g. a catalogue
    product absent from the active release.
    """
    revision_id = _live_revision_id(conn, product_id)
    if revision_id is None:
        raise LookupError(f"product {product_id} has no active revision")
    return revision_id, _load_values(conn, revision_id)


# --- Product states ------------------------------------------------------------------------


def set_product_state(
    conn: Connection,
    release_id: int | None,
    product_id: int,
    state: ProductState,
    active_revision_id: int,
) -> None:
    """Insert or replace the state of a product in a release (``release_id=None`` = user row).

    This is the only way a catalogue product's live revision changes: importers write
    ``add_source_revision(...).live_revision_id`` into their staging release here for every
    outcome (ADOPT, NOOP and REVIEW alike; never the pending revision), and it
    goes live when ``activate_release`` points at that release. Rollback is
    ``activate_release`` of an older release, whose rows are left untouched.
    """
    row = {
        "release_id": release_id,
        "product_id": product_id,
        "status": Status(state.status).value,
        "merged_into": state.merged_into,
        "successors": list(state.successors),
        "active_revision_id": active_revision_id,
    }
    stmt = pg_insert(product_state).values(**row)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_product_state_release_product",
        set_={
            "status": stmt.excluded.status,
            "merged_into": stmt.excluded.merged_into,
            "successors": stmt.excluded.successors,
            "active_revision_id": stmt.excluded.active_revision_id,
        },
    )
    conn.execute(stmt)


def load_states(conn: Connection) -> dict[int, ProductState]:
    """States of the active release plus user-owned states (``release_id IS NULL``)."""
    rows = conn.execute(
        sa.select(
            product_state.c.product_id,
            product_state.c.status,
            product_state.c.merged_into,
            product_state.c.successors,
        )
        .where(
            sa.or_(
                product_state.c.release_id == _active_release_id_subquery(),
                product_state.c.release_id.is_(None),
            )
        )
        .order_by(product_state.c.release_id.asc().nulls_last())
    )
    states: dict[int, ProductState] = {}
    for product_id, status, merged_into, successors in rows:
        states[product_id] = ProductState(Status(status), merged_into, tuple(successors or ()))
    return states
