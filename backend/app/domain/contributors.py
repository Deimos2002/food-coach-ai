"""Top contributors with ranged shares (spec §2.4).

Pure domain logic, no I/O. Unknown values are listed under ``unknown_for`` and are
never summed or ranked.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.item_calc import ItemNutrient

_TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class ItemContribution:
    product_id: int
    name: str
    value: ItemNutrient


@dataclass(frozen=True)
class Contributor:
    product_id: int
    name: str
    lower: Decimal
    upper: Decimal | None
    share: tuple[Decimal, Decimal | None]


def top_contributors(
    items: list[ItemContribution], n: int = 3
) -> tuple[list[Contributor], list[str]]:
    """Group items by ``product_id``, sum their bounds, and rank the top ``n``.

    Items with an unknown value are listed in ``unknown_for`` (by name) and are never
    summed into a group's total nor ranked.
    """
    unknown_for: list[str] = []
    groups: dict[int, dict] = {}
    for item in items:
        if not item.value.known or item.value.range is None:
            unknown_for.append(item.name)
            continue
        group = groups.setdefault(
            item.product_id, {"name": item.name, "lower": Decimal(0), "upper": Decimal(0), "has_upper": True}
        )
        group["lower"] += item.value.range.lower
        if item.value.range.upper is None:
            group["has_upper"] = False
        else:
            if group["has_upper"]:
                group["upper"] += item.value.range.upper

    total_lower = Decimal(0)
    total_upper: Decimal | None = Decimal(0)
    for group in groups.values():
        total_lower += group["lower"]
        if total_upper is not None:
            if group["has_upper"]:
                total_upper += group["upper"]
            else:
                total_upper = None

    contributors: list[Contributor] = []
    for product_id, group in groups.items():
        lower = group["lower"]
        upper = group["upper"] if group["has_upper"] else None

        if total_upper is None:
            share_lower = Decimal(0)
        else:
            share_lower = (lower / total_upper).quantize(_TWO_PLACES) if total_upper != 0 else Decimal(0)

        if total_lower == 0:
            share_upper = None
        else:
            share_upper = (upper / total_lower).quantize(_TWO_PLACES) if upper is not None else None

        contributors.append(
            Contributor(
                product_id=product_id,
                name=group["name"],
                lower=lower,
                upper=upper,
                share=(share_lower, share_upper),
            )
        )

    contributors.sort(key=lambda c: (-c.lower, c.name, c.product_id))
    return contributors[:n], unknown_for
