"""Per-item nutrient calculation.

item = amount_in_basis_unit / 100 * value_per_100, applied to both ends of each range.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.domain.quantity import Quantity, Unit, to_basis_amount
from app.domain.values import Nutrient, NutrientValue, Qualifier, Range

_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class ItemNutrient:
    known: bool
    range: Range | None
    qualifier: Qualifier


def compute_item_nutrients(
    q: Quantity,
    basis_unit: Unit,
    density_g_per_ml: Decimal | None,
    values: Mapping[Nutrient, NutrientValue],
) -> dict[Nutrient, ItemNutrient]:
    lower, upper = to_basis_amount(q, basis_unit, density_g_per_ml)
    lo, hi = lower / _HUNDRED, upper / _HUNDRED
    out: dict[Nutrient, ItemNutrient] = {}
    for nutrient, value in values.items():
        if not value.known or value.range is None:
            out[nutrient] = ItemNutrient(False, None, value.qualifier)
        else:
            out[nutrient] = ItemNutrient(True, value.range.scale(lo, hi), value.qualifier)
    return out
