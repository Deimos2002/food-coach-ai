"""What may be claimed about a day or a period (spec §5.4, as amended by Addendum A).

- Exceedance / reaching a target uses only the lower end of recorded values and is allowed
  on any day. It is worded "Your recorded values ...", never as actual intake.
- Within-limit / low-intake needs ``log_complete`` and ``fields_complete`` and a finite
  upper end. It is worded "Based on your completed log, ...".
- Anything else is "can't tell yet", with reasons.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.domain.day_state import DayNutrientState
from app.domain.values import Range


class LimitKind(StrEnum):
    UPPER_LIMIT = "upper_limit"
    LOWER_TARGET = "lower_target"


class Verdict(StrEnum):
    EXCEEDS = "exceeds"
    WITHIN = "within"
    REACHES = "reaches"
    BELOW = "below"
    CANNOT_TELL = "cannot_tell"


@dataclass(frozen=True)
class Claim:
    verdict: Verdict
    message: str
    reasons: tuple[str, ...]


REASON_DAY_NOT_COMPLETE = "day not marked complete"
REASON_CROSSES = "recorded range crosses the threshold"


def _fmt(d: Decimal) -> str:
    """Plain decimal text without exponent or trailing zeros (7.10 -> 7.1, 400 -> 400)."""
    return format(d.normalize(), "f")


def _cannot_tell(state: DayNutrientState, threshold: Decimal, guideline: str) -> Claim:
    reasons: list[str] = []
    if not state.log_complete:
        reasons.append(REASON_DAY_NOT_COMPLETE)
    unknown = [n for n in state.unresolved if n not in state.additions_undeclared]
    if unknown:
        reasons.append("unknown for: " + ", ".join(unknown))
    if state.unquantified:
        reasons.append("trace (unquantified): " + ", ".join(state.unquantified))
    if state.additions_undeclared:
        reasons.append("cooking additions not declared: " + ", ".join(state.additions_undeclared))
    if not reasons:
        reasons.append(REASON_CROSSES)
    nutrient = _name(state)
    message = (
        f"Can't tell yet how your recorded {nutrient} compares with the {guideline} "
        f"threshold of {_fmt(threshold)}: " + "; ".join(reasons) + "."
    )
    return Claim(Verdict.CANNOT_TELL, message, tuple(reasons))


def _name(state: DayNutrientState) -> str:
    return state.nutrient.value.replace("_", " ")


def _may_use_upper(state: DayNutrientState) -> bool:
    return state.log_complete and state.fields_complete and state.recorded.upper is not None


def evaluate_limit(
    state: DayNutrientState, threshold: Decimal, kind: LimitKind, guideline: str
) -> Claim:
    recorded = state.recorded
    nutrient = _name(state)
    t = _fmt(threshold)
    if kind is LimitKind.UPPER_LIMIT:
        if recorded.lower > threshold:
            return Claim(
                Verdict.EXCEEDS,
                f"Your recorded values exceed the {guideline} {nutrient} guideline of {t} "
                f"(at least {_fmt(recorded.lower)} recorded).",
                (),
            )
        if _may_use_upper(state) and recorded.upper <= threshold:
            return Claim(
                Verdict.WITHIN,
                f"Based on your completed log, your recorded {nutrient} was within the "
                f"{guideline} guideline of {t} (at most {_fmt(recorded.upper)} recorded).",
                (),
            )
        return _cannot_tell(state, threshold, guideline)

    if recorded.lower >= threshold:
        return Claim(
            Verdict.REACHES,
            f"Your recorded values reach the {guideline} {nutrient} target of {t} "
            f"(at least {_fmt(recorded.lower)} recorded).",
            (),
        )
    if _may_use_upper(state) and recorded.upper < threshold:
        return Claim(
            Verdict.BELOW,
            f"Based on your completed log, your recorded {nutrient} was below the "
            f"{guideline} target of {t} (at most {_fmt(recorded.upper)} recorded).",
            (),
        )
    return _cannot_tell(state, threshold, guideline)


@dataclass(frozen=True)
class PeriodSummary:
    included: tuple[date, ...]
    excluded: tuple[date, ...]
    mean: Range | None
    weekday_mean: Range | None
    weekend_mean: Range | None
    message: str


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def _mean(ranges: Sequence[Range]) -> Range | None:
    if not ranges:
        return None
    n = Decimal(len(ranges))
    lower = sum((r.lower for r in ranges), Decimal(0)) / n
    if any(r.upper is None for r in ranges):
        return Range(lower, None)
    upper = sum((r.upper for r in ranges), Decimal(0)) / n
    return Range(lower, upper)


def _dates(ds: Sequence[date]) -> str:
    return ", ".join(d.isoformat() for d in ds)


def period_summary(days: Sequence[tuple[date, DayNutrientState]]) -> PeriodSummary:
    ordered = sorted(days, key=lambda pair: pair[0])
    included = tuple(d for d, s in ordered if s.log_complete)
    excluded = tuple(d for d, s in ordered if not s.log_complete)
    weekday = [s.recorded for d, s in ordered if s.log_complete and not _is_weekend(d)]
    weekend = [s.recorded for d, s in ordered if s.log_complete and _is_weekend(d)]

    excluded_text = (
        f"Excluded (not marked complete): {_dates(excluded)}." if excluded
        else "No days excluded."
    )
    if not included:
        message = f"No completed days in this period, so no average. {excluded_text}"
    else:
        headline = (
            f"Average of recorded values over {len(included)} completed days "
            f"({len(weekday)} weekdays, {len(weekend)} weekend days)"
        )
        parts = [headline]
        if not weekend:
            parts.append("weekend not logged as complete")
        if not weekday:
            parts.append("weekdays not logged as complete")
        message = "; ".join(parts) + f". Included: {_dates(included)}. {excluded_text}"

    return PeriodSummary(
        included=included,
        excluded=excluded,
        mean=_mean([s.recorded for _, s in ordered if s.log_complete]),
        weekday_mean=_mean(weekday),
        weekend_mean=_mean(weekend),
        message=message,
    )
