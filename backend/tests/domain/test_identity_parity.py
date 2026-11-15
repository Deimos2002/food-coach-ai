"""Parity check: app.services.saved's inline resolution agrees with resolve_saved_item.

app/services/saved.py's use_saved_meal cannot call app.domain.identity.resolve_saved_item
directly because that function reports only a string ("blocked: retired" / "blocked:
unit changed") with no successors, and the use-endpoint's contract needs the successor
list. Instead it calls the lower-level `resolve` that resolve_saved_item itself wraps
(identity.py:66-78) and reproduces its unit-mismatch rule inline. This test runs both
paths over the same states and asserts they agree on blocked vs resolved, so a future
change to resolve_saved_item's rule is caught here rather than silently drifting.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.identity import (
    ProductState,
    Resolution,
    SavedItemRef,
    Status,
    resolve,
    resolve_saved_item,
)
from app.domain.quantity import Unit


def _saved_use_resolution(
    ref: SavedItemRef, states: dict[int, ProductState], basis_of: dict[int, Unit]
) -> tuple[bool, int | None, tuple[int, ...]]:
    """Mirrors the inline logic in app/services/saved.py::use_saved_meal."""
    resolution = resolve(ref.product_id, states)
    if not resolution.ok:
        return False, None, resolution.successors
    resolved_unit = basis_of.get(resolution.product_id)
    if resolved_unit is not None and resolved_unit != ref.unit:
        return False, None, ()
    return True, resolution.product_id, ()


def _agree(ref: SavedItemRef, states: dict[int, ProductState], basis_of: dict[int, Unit]) -> None:
    domain_result = resolve_saved_item(ref, states, basis_of)
    ours_ok, ours_product_id, _ = _saved_use_resolution(ref, states, basis_of)
    if isinstance(domain_result, Resolution):
        assert domain_result.ok
        assert ours_ok
        assert domain_result.product_id == ours_product_id
    else:
        assert domain_result.startswith("blocked:")
        assert not ours_ok


def test_active_product_resolves_on_both_paths():
    states = {1: ProductState(Status.ACTIVE)}
    basis_of = {1: Unit.G}
    _agree(SavedItemRef(1, Decimal(100), Unit.G), states, basis_of)


def test_merged_chain_resolves_on_both_paths():
    states = {
        1: ProductState(Status.MERGED, merged_into=2),
        2: ProductState(Status.ACTIVE),
    }
    basis_of = {2: Unit.G}
    _agree(SavedItemRef(1, Decimal(100), Unit.G), states, basis_of)


def test_retired_with_successors_blocks_on_both_paths():
    states = {1: ProductState(Status.RETIRED, successors=(2, 3))}
    basis_of: dict[int, Unit] = {}
    ref = SavedItemRef(1, Decimal(100), Unit.G)
    _agree(ref, states, basis_of)
    domain_result = resolve_saved_item(ref, states, basis_of)
    assert domain_result == "blocked: retired"
    ours_ok, _, ours_successors = _saved_use_resolution(ref, states, basis_of)
    assert not ours_ok
    assert ours_successors == (2, 3)


def test_unit_mismatch_blocks_on_both_paths():
    states = {1: ProductState(Status.ACTIVE)}
    basis_of = {1: Unit.ML}
    ref = SavedItemRef(1, Decimal(100), Unit.G)
    _agree(ref, states, basis_of)
    assert resolve_saved_item(ref, states, basis_of) == "blocked: unit changed"
