"""Weekly rule facts (weekly-coach spec §2.1-2.2).

These facts are the only things the coach may talk about, so they carry the honesty rules
of parent spec A.1: exceedance uses lower bounds and is allowed on any logged day;
"within", "around" and "below" need complete days whose fields are complete; anything else
is "can't tell". A rule cites a guideline card only once the user has verified it (A37);
otherwise the fact is stated as a recorded value with no guideline wording. Sugars are
total sugars used as a proxy and are never called "free sugars" (A11).

The salt threshold is the fixed 5 g of the WHO card; ``Card.threshold`` is not read, so a
stored card value can never silently move a limit. Cards only gate citation.

Pure: no I/O. Decimal only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from app.domain.claims import Claim, LimitKind, Verdict, evaluate_limit
from app.domain.day_state import DayItem, DayNutrientState, day_nutrient_state
from app.domain.day_status import DayStatus
from app.domain.values import Nutrient, Range

MIN_FULL_COVERAGE_DAYS = 3
BAND = Decimal("0.05")
SUGARS_ENERGY_SHARE = Decimal("0.10")
KCAL_PER_G_SUGAR = Decimal(4)
SALT_THRESHOLD_G = Decimal(5)
SUGARS_CARD = "who_free_sugars"
SALT_CARD = "who_salt"

_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class WeekDay:
    day: date
    status: DayStatus
    items: tuple[DayItem, ...]
    target_kcal: int | None
    target_protein_g: int | None


@dataclass(frozen=True)
class Card:
    id: str
    threshold: Decimal | None
    verified: bool


@dataclass(frozen=True)
class RuleFact:
    id: str
    rule: str
    nutrient: Nutrient
    claim: str  # exceeds | within | above | below | around | cannot_tell
    values: dict[str, str]
    days: tuple[date, ...]
    card_id: str | None
    message: str


def iso_week_days(iso_week: str) -> list[date]:
    """Monday to Sunday of ``"YYYY-Www"``; raises ValueError on a bad format or week."""
    m = _WEEK_RE.match(iso_week)
    if not m:
        raise ValueError(f"bad ISO week {iso_week!r}, expected YYYY-Www")
    monday = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    return [monday + timedelta(days=i) for i in range(7)]


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _day(d: date) -> str:
    return f"{_WEEKDAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"


def _days(ds: Sequence[date]) -> str:
    names = [_day(d) for d in ds]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _check_days(iso_week: str, days: Sequence[WeekDay]) -> list[date]:
    week = iso_week_days(iso_week)
    outside = [d.day for d in days if d.day not in week]
    if outside:
        raise ValueError(f"days outside {iso_week}: {_days(sorted(outside))}")
    return week


def week_coverage(iso_week: str, days: Sequence[WeekDay]) -> str:
    """Period statement naming its days, e.g. "4 complete days; Sat 17 Oct not logged"."""
    week = _check_days(iso_week, days)
    by_day = {d.day: d.status for d in days}
    complete = sum(1 for d in week if by_day.get(d) is DayStatus.COMPLETE)
    parts = [f"{complete} complete day{'' if complete == 1 else 's'}"]
    for d in week:
        status = by_day.get(d, DayStatus.NOT_LOGGED)
        if status is DayStatus.PARTIAL:
            parts.append(f"{_day(d)} partial")
        elif status is DayStatus.NOT_LOGGED:
            parts.append(f"{_day(d)} not logged")
    return "; ".join(parts) + "."


def _state(d: WeekDay, nutrient: Nutrient) -> DayNutrientState:
    return day_nutrient_state(d.items, nutrient, log_complete=d.status is DayStatus.COMPLETE)


def _target_rule(
    days: Sequence[WeekDay], nutrient: Nutrient, rule: str, unit: str, below_only: bool
) -> dict | None:
    covered: list[tuple[date, Range, int]] = []
    for d in days:
        target = d.target_kcal if nutrient is Nutrient.ENERGY_KCAL else d.target_protein_g
        if d.status is not DayStatus.COMPLETE or target is None:
            continue
        s = _state(d, nutrient)
        if s.fields_complete and s.recorded.upper is not None:
            covered.append((d.day, s.recorded, target))
    name = "energy" if nutrient is Nutrient.ENERGY_KCAL else "protein"
    dates = tuple(c[0] for c in covered)
    if len(covered) < MIN_FULL_COVERAGE_DAYS:
        return {
            "claim": "cannot_tell", "values": {"full_coverage_days": str(len(covered))},
            "days": dates,
            "message": (
                f"Can't tell yet how your recorded {name} compares with your target: "
                f"only {len(covered)} complete day(s) with every {name} value known, "
                f"{MIN_FULL_COVERAGE_DAYS} needed."
            ),
        }
    n = Decimal(len(covered))
    lower = sum((c[1].lower for c in covered), Decimal(0)) / n
    upper = sum((c[1].upper for c in covered), Decimal(0)) / n
    target = sum((Decimal(c[2]) for c in covered), Decimal(0)) / n
    hi_band, lo_band = target * (1 + BAND), target * (1 - BAND)
    if lower > hi_band:
        claim = "above"
    elif upper < lo_band:
        claim = "below"
    elif lower >= lo_band and upper <= hi_band:
        claim = "around"
    else:
        claim = "cannot_tell"
    if below_only and claim in ("above", "around"):
        return None
    # Displayed bounds are widened outwards so they never overstate precision.
    lo_s = _fmt(lower.quantize(Decimal(1), ROUND_FLOOR))
    hi_s = _fmt(upper.quantize(Decimal(1), ROUND_CEILING))
    t_s = _fmt(target.quantize(Decimal(1), ROUND_HALF_UP))
    values = {"recorded_lower": lo_s, "recorded_upper": hi_s, "target": t_s, "unit": unit}
    on = f"over {_days(dates)}"
    if claim == "cannot_tell":
        message = (
            f"Can't tell yet how your recorded {name} compares with your target of "
            f"{t_s} {unit}: the daily average of {lo_s}-{hi_s} {unit} {on} crosses it."
        )
    else:
        relation = {"above": "above", "below": "below", "around": "around"}[claim]
        average = lo_s if lo_s == hi_s else f"{lo_s}-{hi_s}"
        message = (
            f"Based on your completed log, your recorded {name} averaged {average} "
            f"{unit} a day {on}, {relation} your target of {t_s} {unit}."
        )
    return {"claim": claim, "values": values, "days": dates, "message": message}


def _limit_rule(
    days: Sequence[WeekDay], nutrient: Nutrient, card: Card | None, name: str
) -> dict:
    verified = card is not None and card.verified
    guideline = "WHO" if verified else "reference"
    evaluated: list[tuple[WeekDay, DayNutrientState, Decimal, Claim]] = []
    skipped: list[date] = []
    for d in days:
        if d.status is DayStatus.NOT_LOGGED:
            continue
        if nutrient is Nutrient.SUGARS:
            if d.target_kcal is None:
                skipped.append(d.day)
                continue
            # Exact: targets are whole kcal, so this is a terminating Decimal.
            threshold = Decimal(d.target_kcal) * SUGARS_ENERGY_SHARE / KCAL_PER_G_SUGAR
        else:
            threshold = SALT_THRESHOLD_G
        s = _state(d, nutrient)
        evaluated.append((d, s, threshold, evaluate_limit(s, threshold, LimitKind.UPPER_LIMIT,
                                                          guideline)))
    label = "total sugars (a proxy)" if nutrient is Nutrient.SUGARS else name

    exceeding = [e for e in evaluated if e[3].verdict is Verdict.EXCEEDS]
    if exceeding:
        top = max(exceeding, key=lambda e: e[1].recorded.lower)
        dates = tuple(e[0].day for e in exceeding)
        lo_s = _fmt(top[1].recorded.lower)
        values = {"recorded_lower": lo_s, "threshold": _fmt(top[2]), "unit": "g",
                  "days_exceeding": str(len(exceeding))}
        t_s = _fmt(top[2])
        if len(exceeding) == 1:
            where = f"on {_day(top[0].day)}"
        else:
            where = (f"on {_day(top[0].day)}, the highest of {len(exceeding)} days: "
                     f"{_days(dates)}")
        if verified:
            message = (
                f"Your recorded values exceed the WHO {label} guideline of {t_s} g "
                f"(at least {lo_s} g recorded {where})."
            )
        else:
            message = f"Your recorded {label} reached at least {lo_s} g {where}."
        return {"claim": "exceeds", "values": values, "days": dates, "message": message}

    complete = [e for e in evaluated if e[0].status is DayStatus.COMPLETE]
    if complete and not skipped and all(e[3].verdict is Verdict.WITHIN for e in complete):
        dates = tuple(e[0].day for e in complete)
        hi_s = _fmt(max(e[1].recorded.upper for e in complete))
        t_s = _fmt(min(e[2] for e in complete))
        values = {"recorded_upper": hi_s, "threshold": t_s, "unit": "g"}
        if verified:
            message = (
                f"Based on your completed log, your recorded {label} was within the "
                f"WHO guideline of {t_s} g on each of {_days(dates)} "
                f"(at most {hi_s} g recorded)."
            )
        else:
            message = (
                f"Based on your completed log, your recorded {label} was at most {hi_s} g "
                f"on each of {_days(dates)}."
            )
        return {"claim": "within", "values": values, "days": dates, "message": message}

    reasons: list[str] = []
    if not complete:
        reasons.append("no complete day to check")
    for e in complete:
        if e[3].verdict is not Verdict.WITHIN:
            reasons.append(f"{_day(e[0].day)}: " + "; ".join(e[3].reasons))
    if skipped:
        reasons.append("no energy target on " + _days(skipped))
    message = f"Can't tell yet about your recorded {label} this week: " + "; ".join(reasons) + "."
    dates = tuple(e[0].day for e in evaluated)
    return {"claim": "cannot_tell", "values": {"unit": "g"}, "days": dates, "message": message}


def evaluate_week(
    iso_week: str, days: Sequence[WeekDay], cards: Iterable[Card]
) -> list[RuleFact]:
    """Facts in a stable order (energy, protein, sugars, salt) with ids F1, F2, ...

    The period statement is ``week_coverage(iso_week, days)``; messages do not repeat it.
    Raises ValueError if a day falls outside ``iso_week``.
    """
    _check_days(iso_week, days)
    if not days:
        return []
    ordered = sorted(days, key=lambda d: d.day)
    by_id = {c.id: c for c in cards}

    raw: list[tuple[str, Nutrient, str | None, dict | None]] = [
        ("energy_vs_target", Nutrient.ENERGY_KCAL, None,
         _target_rule(ordered, Nutrient.ENERGY_KCAL, "energy_vs_target", "kcal", False)),
        ("protein_vs_target", Nutrient.PROTEIN, None,
         _target_rule(ordered, Nutrient.PROTEIN, "protein_vs_target", "g", True)),
    ]
    for rule, nutrient, card_id, name in (
        ("sugars_daily", Nutrient.SUGARS, SUGARS_CARD, "sugars"),
        ("salt_daily", Nutrient.SALT, SALT_CARD, "salt"),
    ):
        card = by_id.get(card_id)
        cited = card_id if card is not None and card.verified else None
        raw.append((rule, nutrient, cited, _limit_rule(ordered, nutrient, card, name)))

    facts: list[RuleFact] = []
    for rule, nutrient, card_id, r in raw:
        if r is None:
            continue
        facts.append(RuleFact(
            id=f"F{len(facts) + 1}", rule=rule, nutrient=nutrient, claim=r["claim"],
            values=r["values"], days=r["days"], card_id=card_id,
            message=r["message"],
        ))
    return facts
