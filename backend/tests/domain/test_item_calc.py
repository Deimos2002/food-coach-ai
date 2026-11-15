from decimal import Decimal

import pytest

from app.domain.item_calc import ItemNutrient, compute_item_nutrients
from app.domain.quantity import Quantity, QuantitySource, Unit, UnitMismatch, to_basis_amount
from app.domain.values import Nutrient, NutrientValue, Qualifier, Range, Source

D = Decimal


def test_a24_250ml_of_11g_per_100ml_is_27_5g():
    vals = {Nutrient.SUGARS: NutrientValue.point(D("11"), Source.LABEL)}
    out = compute_item_nutrients(
        Quantity.recorded(D("250"), Unit.ML, QuantitySource.PACKAGE), Unit.ML, None, vals
    )
    assert out[Nutrient.SUGARS].range == Range(D("27.5"), D("27.5"))


def test_a25_grams_for_ml_product_without_density_rejected():
    with pytest.raises(UnitMismatch):
        to_basis_amount(Quantity.recorded(D("100"), Unit.G, QuantitySource.WEIGHED), Unit.ML, None)


def test_grams_for_ml_product_with_density_converts():
    q = Quantity.recorded(D("103"), Unit.G, QuantitySource.WEIGHED)
    assert to_basis_amount(q, Unit.ML, D("1.03")) == (D("100"), D("100"))


def test_a6_estimated_range_propagates():
    q = Quantity.estimated(D("200"), D("140"), D("260"), Unit.G, QuantitySource.PHOTO_ESTIMATE)
    vals = {Nutrient.CARBOHYDRATE: NutrientValue.point(D("28"), Source.CIQUAL)}
    assert compute_item_nutrients(q, Unit.G, None, vals)[Nutrient.CARBOHYDRATE].range == Range(
        D("39.2"), D("72.8")
    )


def test_unknown_stays_unknown_not_zero():
    out = compute_item_nutrients(
        Quantity.recorded(D("250"), Unit.ML, QuantitySource.PACKAGE),
        Unit.ML,
        None,
        {Nutrient.CAFFEINE: NutrientValue.unknown(Source.LABEL)},
    )
    assert out[Nutrient.CAFFEINE] == ItemNutrient(False, None, Qualifier.NONE)


def test_trace_has_no_upper_after_scaling():
    v = NutrientValue(True, Range(D("0"), None), Qualifier.TRACE, Source.CIQUAL)
    out = compute_item_nutrients(
        Quantity.recorded(D("50"), Unit.G, QuantitySource.WEIGHED), Unit.G, None, {Nutrient.SALT: v}
    )
    assert out[Nutrient.SALT] == ItemNutrient(True, Range(D("0"), None), Qualifier.TRACE)
