from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from statistics import median


@dataclass(frozen=True)
class WeightEntry:
    day: date
    kg: Decimal
    confirmed: bool = False


@dataclass(frozen=True)
class TrendPoint:
    day: date
    trend: Decimal
    flagged: bool
    suggest_confirm: bool


def compute_trend(entries: Sequence[WeightEntry]) -> list[TrendPoint]:
    included: list[Decimal] = []
    result: list[TrendPoint] = []
    trend = Decimal(0)
    previous_side = 0
    for entry in sorted(entries, key=lambda entry: entry.day):
        difference = entry.kg - trend
        flagged = len(included) >= 5 and not entry.confirmed and abs(difference) > Decimal(2)
        side = (1 if difference > 0 else -1) if flagged else 0
        suggest_confirm = flagged and side == previous_side
        if not flagged:
            included.append(entry.kg)
            if len(included) <= 3:
                trend = median(included)
            else:
                trend += Decimal("0.1") * (entry.kg - trend)
        result.append(
            TrendPoint(
                entry.day,
                trend.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                flagged,
                suggest_confirm,
            )
        )
        previous_side = side
    return result
