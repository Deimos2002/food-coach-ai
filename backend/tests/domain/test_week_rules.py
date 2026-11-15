from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.day_state import DayItem
from app.domain.day_status import DayStatus
from app.domain.item_calc import ItemNutrient
from app.domain.values import Nutrient, Qualifier, Range
from app.domain.week_rules import (
    Card,
    WeekDay,
    evaluate_week,
    iso_week_days,
    week_coverage,
)

D = Decimal
MON = date(2026, 10, 12)  # 2026-W42
WEEK = "2026-W42"
VERIFIED = [Card("who_free_sugars", None, True), Card("who_salt", D(5), True)]
UNVERIFIED = [Card("who_free_sugars", None, False), Card("who_salt", D(5), False)]


def known(lo: str, hi: str | None = None) -> ItemNutrient:
    return ItemNutrient(True, Range(D(lo), D(hi if hi is not None else lo)), Qualifier.NONE)


UNKNOWN = ItemNutrient(False, None, Qualifier.NONE)


def day(
    offset: int,
    status: DayStatus = DayStatus.COMPLETE,
    kcal: tuple[str, str] | None = ("2000", "2000"),
    protein: tuple[str, str] | None = ("120", "120"),
    sugars: tuple[str, str] | None = ("20", "20"),
    salt: tuple[str, str] | None = ("3", "3"),
    target_kcal: int | None = 2000,
    target_protein_g: int | None = 130,
) -> WeekDay:
    nutrients = {}
    for n, v in (
        (Nutrient.ENERGY_KCAL, kcal),
        (Nutrient.PROTEIN, protein),
        (Nutrient.SUGARS, sugars),
        (Nutrient.SALT, salt),
    ):
        nutrients[n] = UNKNOWN if v is None else known(*v)
    return WeekDay(
        MON + timedelta(days=offset),
        status,
        (DayItem("Food", nutrients),),
        target_kcal,
        target_protein_g,
    )


def facts_for(facts, rule):
    return [f for f in facts if f.rule == rule]


def test_iso_week_days_handles_year_boundary():
    w53 = iso_week_days("2026-W53")
    assert w53[0] == date(2026, 12, 28)
    assert w53[-1] == date(2027, 1, 3)
    assert len(w53) == 7
    assert iso_week_days("2027-W01")[0] == date(2027, 1, 4)
    for bad in ("2026-53", "2025-W53", "2026-W00", "x"):
        with pytest.raises(ValueError):
            iso_week_days(bad)


def test_energy_cannot_tell_under_3_full_coverage_days():
    days = [day(0, kcal=("3000", "3000")), day(1, kcal=("3000", "3000")),
            day(2, kcal=None), day(3, status=DayStatus.PARTIAL, kcal=("3000", "3000"))]
    (fact,) = facts_for(evaluate_week(WEEK, days, VERIFIED), "energy_vs_target")
    assert fact.claim == "cannot_tell"


def test_energy_above_target_uses_lower_bound():
    # Lower mean 2200 > 2100 (2000 x 1.05): above, whatever the upper bound.
    days = [day(i, kcal=("2200", "2600")) for i in range(3)]
    (fact,) = facts_for(evaluate_week(WEEK, days, VERIFIED), "energy_vs_target")
    assert fact.claim == "above"
    assert fact.values["recorded_lower"] == "2200"
    # Lower mean 2050 is inside the band: the range crosses it, so no "above".
    days = [day(i, kcal=("2050", "2600")) for i in range(3)]
    (fact,) = facts_for(evaluate_week(WEEK, days, VERIFIED), "energy_vs_target")
    assert fact.claim == "cannot_tell"
    days = [day(i, kcal=("1950", "2050")) for i in range(3)]
    assert facts_for(evaluate_week(WEEK, days, VERIFIED), "energy_vs_target")[0].claim == "around"
    days = [day(i, kcal=("1500", "1800")) for i in range(3)]
    assert facts_for(evaluate_week(WEEK, days, VERIFIED), "energy_vs_target")[0].claim == "below"


def test_protein_only_below_emitted():
    high = [day(i, protein=("200", "210")) for i in range(3)]
    assert facts_for(evaluate_week(WEEK, high, VERIFIED), "protein_vs_target") == []
    around = [day(i, protein=("128", "132")) for i in range(3)]
    assert facts_for(evaluate_week(WEEK, around, VERIFIED), "protein_vs_target") == []
    low = [day(i, protein=("80", "90")) for i in range(3)]
    (fact,) = facts_for(evaluate_week(WEEK, low, VERIFIED), "protein_vs_target")
    assert fact.claim == "below"
    for f in evaluate_week(WEEK, high + low, VERIFIED):
        if f.rule == "protein_vs_target":
            assert f.claim in ("below", "cannot_tell")


def test_sugar_exceeds_on_partial_day_lower_bound():
    days = [day(0), day(1, status=DayStatus.PARTIAL, sugars=("86", "120")),
            day(2, sugars=("60", "70"))]
    (fact,) = facts_for(evaluate_week(WEEK, days, VERIFIED), "sugars_daily")
    assert fact.claim == "exceeds"
    assert fact.days == (MON + timedelta(days=1), MON + timedelta(days=2))
    assert fact.values["recorded_lower"] == "86"
    assert fact.values["threshold"] == "50"  # 2000 x 0.10 / 4
    assert fact.card_id == "who_free_sugars"


def test_sugar_within_requires_all_days_full_coverage():
    ok = [day(i) for i in range(3)]
    (fact,) = facts_for(evaluate_week(WEEK, ok, VERIFIED), "sugars_daily")
    assert fact.claim == "within"
    gap = ok + [day(3, sugars=None)]
    (fact,) = facts_for(evaluate_week(WEEK, gap, VERIFIED), "sugars_daily")
    assert fact.claim == "cannot_tell"
    # A day with no target is skipped, not treated as within.
    (fact,) = facts_for(evaluate_week(WEEK, [day(0, target_kcal=None)], VERIFIED), "sugars_daily")
    assert fact.claim == "cannot_tell"


def test_unverified_card_gives_recorded_fact_wording_only():
    days = [day(0), day(1, sugars=("86", "90")), day(2, salt=("7", "8"))]
    facts = evaluate_week(WEEK, days, UNVERIFIED)
    sugar = facts_for(facts, "sugars_daily")[0]
    assert sugar.card_id is None
    assert sugar.message.startswith(
        "Your recorded total sugars (a proxy) reached at least 86 g on Tue 13 Oct."
    )
    for f in facts:
        assert "guideline" not in f.message.lower()
        assert "WHO" not in f.message
    verified = facts_for(evaluate_week(WEEK, days, VERIFIED), "salt_daily")[0]
    assert verified.card_id == "who_salt"
    assert "Your recorded values exceed" in verified.message


def test_no_free_sugars_wording():
    days = [day(0, sugars=("86", "90")), day(1), day(2), day(3, sugars=None)]
    for cards in (VERIFIED, UNVERIFIED):
        for f in evaluate_week(WEEK, days, cards):
            assert "free sugar" not in f.message.lower()
            assert "you ate" not in f.message.lower()


def test_period_message_names_missing_days():
    days = [day(i) for i in range(4)] + [day(4, status=DayStatus.PARTIAL)]
    facts = evaluate_week(WEEK, days, VERIFIED)
    assert [f.id for f in facts] == [f"F{i}" for i in range(1, len(facts) + 1)]
    coverage = week_coverage(WEEK, days)
    assert "4 complete days" in coverage
    assert "Fri 16 Oct partial" in coverage
    assert "Sat 17 Oct not logged" in coverage
    assert "Sun 18 Oct not logged" in coverage
    for f in facts:
        assert "not logged" not in f.message


def test_sugar_threshold_compared_unrounded():
    # 2150 kcal x 0.10 / 4 = 53.75 g; 53.9 g must not read as within.
    days = [day(i, target_kcal=2150) for i in range(2)]
    days.append(day(2, target_kcal=2150, sugars=("53.9", "53.9")))
    (fact,) = [f for f in evaluate_week(WEEK, days, VERIFIED) if f.rule == "sugars_daily"]
    assert fact.claim == "exceeds"
    assert fact.values["threshold"] == "53.75"
    assert "53.75 g" in fact.message
    assert "at least 53.9 g recorded" in fact.message


def test_highest_day_named_when_several_exceed():
    days = [day(0, salt=("6", "6")), day(1, salt=("9", "9")), day(2)]
    for cards in (VERIFIED, UNVERIFIED):
        (fact,) = [f for f in evaluate_week(WEEK, days, cards) if f.rule == "salt_daily"]
        assert "at least 9 g" in fact.message
        assert "highest" in fact.message


def test_day_outside_week_rejected():
    with pytest.raises(ValueError):
        evaluate_week(WEEK, [day(0), day(7)], VERIFIED)
    with pytest.raises(ValueError):
        week_coverage(WEEK, [day(-1)])
