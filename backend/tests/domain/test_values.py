"""Range hardening (final fixes F3): no non-finite bounds, no negative lower bound."""

from decimal import Decimal

import pytest

from app.domain.quantity import Quantity, QuantitySource, Unit
from app.domain.values import Range


@pytest.mark.parametrize("bad", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_range_rejects_non_finite_lower(bad):
    with pytest.raises(ValueError):
        Range(Decimal(bad), None)


@pytest.mark.parametrize("bad", ["NaN", "Infinity"])
def test_range_rejects_non_finite_upper(bad):
    with pytest.raises(ValueError):
        Range(Decimal(0), Decimal(bad))


def test_range_rejects_negative_lower():
    with pytest.raises(ValueError):
        Range(Decimal("-0.1"), Decimal(1))


def test_range_allows_zero_point_and_unbounded():
    assert Range.point(Decimal(0)).upper == Decimal(0)
    assert Range(Decimal(0), None).upper is None


@pytest.mark.parametrize("bad", ["NaN", "Infinity"])
def test_quantity_rejects_non_finite(bad):
    with pytest.raises(ValueError):
        Quantity.recorded(Decimal(bad), Unit.G, QuantitySource.WEIGHED)
