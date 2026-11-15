"""Diagnostic energy consistency check for EU nutrition labels."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from app.domain.values import Range


@dataclass(frozen=True)
class LabelFigures:
    energy_kcal: Decimal | None
    carbohydrate: Decimal | None
    protein: Decimal | None
    fat: Decimal | None
    fibre: Decimal | None
    polyols: Decimal | None
    erythritol: Decimal | None
    alcohol: Decimal | None
    label_format: Literal["eu", "us", "unknown"]
    has_salatrims: bool = False


class EnergyResult(StrEnum):
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class EnergyCheck:
    result: EnergyResult
    computed: Range | None
    reasons: tuple[str, ...]


def check_energy(f: LabelFigures) -> EnergyCheck:
    reasons = []
    if f.label_format != "eu":
        reasons.append("label format is not EU")
    if f.has_salatrims:
        reasons.append("salatrims are present")
    for name in ("energy_kcal", "carbohydrate", "protein", "fat", "fibre"):
        if getattr(f, name) is None:
            reasons.append(f"missing {name}")
    if reasons:
        return EnergyCheck(EnergyResult.INCOMPLETE, None, tuple(reasons))

    polyols = f.polyols if f.polyols is not None else Decimal(0)
    alcohol = f.alcohol if f.alcohol is not None else Decimal(0)
    erythritol_low = f.erythritol if f.erythritol is not None else Decimal(0)
    erythritol_high = f.erythritol if f.erythritol is not None else polyols
    base = (
        Decimal(4) * (f.carbohydrate - polyols)
        + Decimal(4) * f.protein
        + Decimal(9) * f.fat
        + Decimal(2) * f.fibre
        + Decimal(7) * alcohol
    )
    computed = Range(
        base + Decimal("2.4") * (polyols - erythritol_high),
        base + Decimal("2.4") * (polyols - erythritol_low),
    )
    lower = computed.lower - max(computed.lower * Decimal("0.1"), Decimal(5))
    upper = computed.upper + max(computed.upper * Decimal("0.1"), Decimal(5))
    if lower <= f.energy_kcal <= upper:
        return EnergyCheck(EnergyResult.CONSISTENT, computed, ())
    return EnergyCheck(
        EnergyResult.INCONSISTENT,
        computed,
        ("declared energy is outside the rounding interval",),
    )
