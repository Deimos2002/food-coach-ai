from decimal import Decimal

import pytest

from app.domain.identity import (
    LoggedRef,
    ProductState,
    Resolution,
    SavedItemRef,
    Status,
    items_to_flag_after_rollback,
    resolve,
    resolve_saved_item,
)
from app.domain.quantity import Unit

D = Decimal


def test_merge_redirect_resolves():
    states = {
        2: ProductState(status=Status.MERGED, merged_into=1),
        1: ProductState(status=Status.ACTIVE),
    }
    assert resolve(2, states) == Resolution(True, 1)


def test_a12_saved_item_blocks_on_split():
    states = {
        5: ProductState(status=Status.RETIRED, successors=(6, 7)),
    }
    ref = SavedItemRef(product_id=5, amount=D("100"), unit=Unit.G)
    basis_of = {5: Unit.G, 6: Unit.G, 7: Unit.G}
    assert resolve_saved_item(ref, states, basis_of) == "blocked: retired"


def test_saved_item_blocks_on_unit_change():
    states = {
        1: ProductState(status=Status.ACTIVE),
    }
    ref = SavedItemRef(product_id=1, amount=D("100"), unit=Unit.ML)
    basis_of = {1: Unit.G}
    assert resolve_saved_item(ref, states, basis_of) == "blocked: unit changed"


def test_a23_bad_merge_rolled_back_flags_new_log():
    restored_states = {
        10: ProductState(status=Status.ACTIVE),
        11: ProductState(status=Status.ACTIVE),
    }
    items = [
        LoggedRef(item_id=99, requested_product_id=10, resolved_product_id=11, release_id=2),
        LoggedRef(item_id=98, requested_product_id=10, resolved_product_id=10, release_id=1),
    ]
    assert items_to_flag_after_rollback(items, 2, restored_states) == [99]


def test_cycle_raises():
    states = {
        1: ProductState(status=Status.MERGED, merged_into=2),
        2: ProductState(status=Status.MERGED, merged_into=1),
    }
    with pytest.raises(ValueError):
        resolve(1, states)
