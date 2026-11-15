from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal


@dataclass(frozen=True)
class Profile:
    sex: Literal["male", "female"]
    birth_year: int
    height_cm: Decimal
    activity: Decimal
    goal: Literal["lose", "maintain", "gain"]
    pace_pct_per_week: Decimal


@dataclass(frozen=True)
class Targets:
    maintenance_kcal: int
    kcal: int
    protein_g: int


def compute_targets(p: Profile, trend_kg: Decimal, today: date) -> Targets:
    age = today.year - p.birth_year
    bmr = (
        Decimal(10) * trend_kg
        + Decimal("6.25") * p.height_cm
        - Decimal(5) * age
        + (Decimal(5) if p.sex == "male" else Decimal(-161))
    )
    maintenance = bmr * p.activity
    pace = min(p.pace_pct_per_week, Decimal(1))
    delta = min(
        pace / Decimal(100) * trend_kg * Decimal(7700) / Decimal(7), maintenance * Decimal("0.25")
    )
    kcal = maintenance
    if p.goal == "lose":
        kcal -= delta
    elif p.goal == "gain":
        kcal += delta
    return Targets(
        int(maintenance.quantize(Decimal(1), rounding=ROUND_HALF_UP)),
        int(kcal.quantize(Decimal(1), rounding=ROUND_HALF_UP)),
        int((Decimal("1.8") * trend_kg).quantize(Decimal(1), rounding=ROUND_HALF_UP)),
    )
