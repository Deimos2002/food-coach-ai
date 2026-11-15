"""Identity resolution across catalogue merges/splits, and rollback flagging.

Pure domain: no I/O. Follows ``merged_into`` chains to resolve a requested product id
to its current product, detects cycles, and decides which logged items need user
attention after a catalogue release rollback (Addendum A).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.domain.quantity import Unit


class Status(StrEnum):
    ACTIVE = "active"
    MERGED = "merged"
    RETIRED = "retired"


@dataclass(frozen=True)
class ProductState:
    status: Status
    merged_into: int | None = None
    successors: tuple[int, ...] = ()


@dataclass(frozen=True)
class Resolution:
    ok: bool
    product_id: int
    successors: tuple[int, ...] = field(default=())


def resolve(product_id: int, states: Mapping[int, ProductState]) -> Resolution:
    """Follow ``merged_into`` chains until an ACTIVE or RETIRED product is reached."""
    seen: set[int] = set()
    current = product_id
    while True:
        if current in seen:
            raise ValueError(f"cycle detected resolving product {product_id}")
        seen.add(current)
        state = states.get(current)
        if state is None or state.status == Status.ACTIVE:
            return Resolution(True, current)
        if state.status == Status.MERGED:
            if state.merged_into is None:
                raise ValueError(f"product {current} is MERGED but has no merged_into")
            current = state.merged_into
            continue
        if state.status == Status.RETIRED:
            return Resolution(False, current, state.successors)
        raise ValueError(f"unknown status {state.status!r} for product {current}")


@dataclass(frozen=True)
class SavedItemRef:
    product_id: int
    amount: Decimal
    unit: Unit


def resolve_saved_item(
    ref: SavedItemRef,
    states: Mapping[int, ProductState],
    basis_of: Mapping[int, Unit],
) -> Resolution | str:
    """Resolve a saved item's product reference, or report why the user must choose."""
    resolution = resolve(ref.product_id, states)
    if not resolution.ok:
        return "blocked: retired"
    resolved_unit = basis_of.get(resolution.product_id)
    if resolved_unit is not None and resolved_unit != ref.unit:
        return "blocked: unit changed"
    return resolution


@dataclass(frozen=True)
class LoggedRef:
    item_id: int
    requested_product_id: int
    resolved_product_id: int
    release_id: int | None


def items_to_flag_after_rollback(
    items: Sequence[LoggedRef],
    rolled_back_release_id: int,
    restored_states: Mapping[int, ProductState],
) -> list[int]:
    """Ids of items logged under the rolled-back release whose resolution now differs."""
    flagged: list[int] = []
    for item in items:
        if item.release_id != rolled_back_release_id:
            continue
        resolution = resolve(item.requested_product_id, restored_states)
        if resolution.product_id != item.resolved_product_id:
            flagged.append(item.item_id)
    return flagged
