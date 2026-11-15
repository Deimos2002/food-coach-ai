"""Per-nutrient day state.

The recorded range is the sum of what was recorded; it says nothing about what was not.
Unknown values are never treated as 0: they are skipped from the sum and their items are
listed as unresolved. A known value with no upper end (e.g. a trace) adds its lower end
to the sum, makes the total's upper end None, and is listed as unquantified, whatever its
qualifier says (spec A.2: "trace (unquantified)"). A dish whose cooking additions were not
declared "yes" is unresolved for the nutrients in its ``unresolved_for`` and is also listed
in ``additions_undeclared`` so the reason can say so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.domain.item_calc import ItemNutrient
from app.domain.values import Nutrient, Range

_ZERO = Decimal(0)


@dataclass(frozen=True)
class DayItem:
    name: str
    nutrients: Mapping[Nutrient, ItemNutrient]
    # Nutrients this item may contain without them being recorded, e.g. salt and fat for a
    # dish whose ``includes_cooking_additions`` is not "yes".
    unresolved_for: frozenset[Nutrient] = frozenset()


@dataclass(frozen=True)
class DayNutrientState:
    nutrient: Nutrient
    recorded: Range
    unresolved: tuple[str, ...]
    fields_complete: bool
    log_complete: bool
    # Known values with no upper end (traces); disjoint from ``unresolved``.
    unquantified: tuple[str, ...] = ()
    # Subset of ``unresolved``: known values blocked only by undeclared cooking additions.
    additions_undeclared: tuple[str, ...] = ()


def day_nutrient_state(
    items: Sequence[DayItem], nutrient: Nutrient, log_complete: bool
) -> DayNutrientState:
    recorded = Range(_ZERO, _ZERO)
    unresolved: list[str] = []
    unquantified: list[str] = []
    additions_undeclared: list[str] = []
    for item in items:
        value = item.nutrients.get(nutrient)
        # Unknown wins over any qualifier: an unknown value never contributes to the sum.
        if value is None or not value.known or value.range is None:
            unresolved.append(item.name)
            continue
        recorded = recorded + value.range
        if nutrient in item.unresolved_for:
            unresolved.append(item.name)
            additions_undeclared.append(item.name)
        elif value.range.upper is None:
            unquantified.append(item.name)
    return DayNutrientState(
        nutrient=nutrient,
        recorded=recorded,
        unresolved=tuple(unresolved),
        fields_complete=not unresolved and not unquantified,
        log_complete=log_complete,
        unquantified=tuple(unquantified),
        additions_undeclared=tuple(additions_undeclared),
    )
