"""Source revision decisions and pinned-override reconfirmation.

Pure domain logic: decides whether a new source record for a product becomes a new
nutrient-profile revision automatically (ADOPT), is identical (NOOP), or needs human
review (REVIEW); and splits user overrides into those that still apply after a
revision and those that need the user to reconfirm them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domain.values import Nutrient, NutrientValue, Range

FLOORS: dict[Nutrient, Decimal] = {
    Nutrient.ENERGY_KCAL: Decimal(10),
    Nutrient.SUGARS: Decimal("0.5"),
    Nutrient.FAT: Decimal("0.5"),
    Nutrient.PROTEIN: Decimal("0.5"),
    Nutrient.SALT: Decimal("0.05"),
}

_DEFAULT_FLOOR = Decimal("0.5")

_RELATIVE_CHANGE_THRESHOLD = Decimal("0.2")


def _floor_for(nutrient: Nutrient) -> Decimal:
    return FLOORS.get(nutrient, _DEFAULT_FLOOR)


def _midpoint(range_: Range) -> Decimal:
    if range_.upper is None:
        return range_.lower
    return (range_.lower + range_.upper) / Decimal(2)


def _decimal_text(d: Decimal) -> str:
    """Numerically-equal Decimals (1.0 vs 1.00) normalise to the same text."""
    normalized = d.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def _nutrient_value_to_jsonable(value: NutrientValue) -> dict[str, object]:
    if not value.known or value.range is None:
        return {"known": False, "lower": None, "upper": None, "qualifier": value.qualifier.value}
    return {
        "known": True,
        "lower": _decimal_text(value.range.lower),
        "upper": None if value.range.upper is None else _decimal_text(value.range.upper),
        "qualifier": value.qualifier.value,
    }


def nutrition_hash(values: Mapping[Nutrient, NutrientValue]) -> str:
    """SHA-256 of a canonical JSON of the nutrient fields only."""
    data = {nutrient.value: _nutrient_value_to_jsonable(v) for nutrient, v in values.items()}
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Action(StrEnum):
    NOOP = "noop"
    ADOPT = "adopt"
    REVIEW = "review"


@dataclass(frozen=True)
class RevisionDecision:
    action: Action
    reasons: tuple[str, ...]


def decide_revision(
    current: Mapping[Nutrient, NutrientValue] | None,
    new: Mapping[Nutrient, NutrientValue],
) -> RevisionDecision:
    if current is None:
        return RevisionDecision(Action.ADOPT, ())

    if nutrition_hash(current) == nutrition_hash(new):
        return RevisionDecision(Action.NOOP, ())

    reasons: list[str] = []
    all_nutrients = sorted(set(current) | set(new), key=lambda n: n.value)
    for nutrient in all_nutrients:
        old_value = current.get(nutrient)
        new_value = new.get(nutrient)
        old_known = old_value is not None and old_value.known
        new_known = new_value is not None and new_value.known

        if old_known and not new_known:
            reasons.append(f"{nutrient.value}: known→unknown")
            continue

        if old_known and new_known:
            assert old_value is not None and old_value.range is not None
            assert new_value is not None and new_value.range is not None
            old_mid = _midpoint(old_value.range)
            new_mid = _midpoint(new_value.range)
            floor = _floor_for(nutrient)
            if old_mid < floor and new_mid < floor:
                continue
            if old_mid == 0:
                # A 0 -> above-floor change is always more than a 20% relative change.
                reasons.append(f"{nutrient.value}: {old_mid}→{new_mid}")
                continue
            rel_change = abs(new_mid - old_mid) / old_mid
            if rel_change > _RELATIVE_CHANGE_THRESHOLD:
                reasons.append(f"{nutrient.value}: {old_mid}→{new_mid}")

    if reasons:
        return RevisionDecision(Action.REVIEW, tuple(reasons))
    return RevisionDecision(Action.ADOPT, ())


@dataclass(frozen=True)
class Override:
    id: int
    version: int
    nutrient: Nutrient
    value: NutrientValue
    pinned_revision_id: int
    retain_across_revisions: bool


def split_overrides(
    overrides: Sequence[Override], active_revision_id: int
) -> tuple[list[Override], list[Override]]:
    """Split overrides into (applied, needs_reconfirm) against the active revision."""
    applied: list[Override] = []
    needs_reconfirm: list[Override] = []
    for override in overrides:
        if override.retain_across_revisions or override.pinned_revision_id == active_revision_id:
            applied.append(override)
        else:
            needs_reconfirm.append(override)
    return applied, needs_reconfirm
