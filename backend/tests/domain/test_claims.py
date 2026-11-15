from datetime import date, timedelta
from decimal import Decimal

from app.domain.claims import (
    LimitKind,
    Verdict,
    evaluate_limit,
    period_summary,
)
from app.domain.day_state import DayItem, day_nutrient_state
from app.domain.item_calc import ItemNutrient
from app.domain.values import Nutrient, Qualifier, Range

D = Decimal


def known(lo: str, hi: str | None, qualifier: Qualifier = Qualifier.NONE) -> ItemNutrient:
    return ItemNutrient(True, Range(D(lo), None if hi is None else D(hi)), qualifier)


UNKNOWN = ItemNutrient(False, None, Qualifier.NONE)


def test_a1_zero_kcal_drink_unknown_caffeine():
    items = [
        DayItem("Lunch", {Nutrient.CAFFEINE: known("0", "0")}),
        DayItem("Red Bull Peach", {Nutrient.CAFFEINE: UNKNOWN}),
    ]
    state = day_nutrient_state(items, Nutrient.CAFFEINE, log_complete=True)
    assert state.fields_complete is False
    assert "Red Bull Peach" in state.unresolved
    claim = evaluate_limit(state, D("400"), LimitKind.UPPER_LIMIT, "EFSA")
    assert claim.verdict is Verdict.CANNOT_TELL


def test_a2_partial_day_exceedance():
    items = [
        DayItem("Breakfast", {Nutrient.SALT: known("3.1", "3.2")}),
        DayItem("Lunch", {Nutrient.SALT: known("4.0", "4.2")}),
    ]
    state = day_nutrient_state(items, Nutrient.SALT, log_complete=False)
    assert state.recorded == Range(D("7.1"), D("7.4"))
    claim = evaluate_limit(state, D("5"), LimitKind.UPPER_LIMIT, "WHO")
    assert claim.verdict is Verdict.EXCEEDS
    assert claim.message.startswith("Your recorded values exceed")


def test_a3_partial_day_low_fibre_not_claimed():
    items = [DayItem("Lunch", {Nutrient.FIBRE: known("12", "12")})]
    state = day_nutrient_state(items, Nutrient.FIBRE, log_complete=False)
    claim = evaluate_limit(state, D("30"), LimitKind.LOWER_TARGET, "EFSA")
    assert claim.verdict is Verdict.CANNOT_TELL
    assert "day not marked complete" in claim.reasons


def test_a4_a30_dish_without_additions_blocks_salt():
    items = [
        DayItem(
            "Homemade curry",
            {Nutrient.SALT: known("1", "1")},
            unresolved_for=frozenset({Nutrient.SALT, Nutrient.FAT}),
        )
    ]
    state = day_nutrient_state(items, Nutrient.SALT, log_complete=True)
    assert state.fields_complete is False
    assert "Homemade curry" in state.unresolved
    claim = evaluate_limit(state, D("5"), LimitKind.UPPER_LIMIT, "WHO")
    assert claim.verdict is Verdict.CANNOT_TELL
    assert "cooking additions not declared: Homemade curry" in claim.reasons


def test_a26_trace_blocks_within():
    items = [
        DayItem("Bread", {Nutrient.SALT: known("2", "2")}),
        DayItem("Cheese", {Nutrient.SALT: known("0", None, Qualifier.TRACE)}),
    ]
    state = day_nutrient_state(items, Nutrient.SALT, log_complete=True)
    assert state.recorded.upper is None
    claim = evaluate_limit(state, D("5"), LimitKind.UPPER_LIMIT, "WHO")
    assert claim.verdict is Verdict.CANNOT_TELL


def test_within_on_complete_day():
    items = [
        DayItem("Bread", {Nutrient.SALT: known("1", "1.5")}),
        DayItem("Soup", {Nutrient.SALT: known("2", "2")}),
    ]
    state = day_nutrient_state(items, Nutrient.SALT, log_complete=True)
    assert state.recorded == Range(D("3"), D("3.5"))
    claim = evaluate_limit(state, D("5"), LimitKind.UPPER_LIMIT, "WHO")
    assert claim.verdict is Verdict.WITHIN
    assert claim.message.startswith("Based on your completed log, ")


def test_a5_period_excludes_unlogged_weekend():
    monday = date(2026, 10, 5)
    assert monday.weekday() == 0
    days = []
    for i in range(14):
        d = monday + timedelta(days=i)
        is_weekend = d.weekday() >= 5
        items = [DayItem("Meal", {Nutrient.SALT: known("4", "4")})]
        days.append((d, day_nutrient_state(items, Nutrient.SALT, log_complete=not is_weekend)))
    summary = period_summary(days)
    assert len(summary.included) == 10
    assert len(summary.excluded) == 4
    assert summary.weekend_mean is None
    assert summary.weekday_mean == Range(D("4"), D("4"))
    assert "weekend not logged" in summary.message


# --- Guards for gaps in Task 1's NutrientValue invariants ---------------------------------


def test_unbounded_known_value_without_trace_qualifier_still_blocks_within():
    items = [
        DayItem("Bread", {Nutrient.SALT: known("2", "2")}),
        DayItem("Sauce", {Nutrient.SALT: known("0.1", None, Qualifier.NONE)}),
    ]
    state = day_nutrient_state(items, Nutrient.SALT, log_complete=True)
    assert state.recorded == Range(D("2.1"), None)
    assert "Sauce" in state.unquantified
    assert "Sauce" not in state.unresolved
    assert state.fields_complete is False
    claim = evaluate_limit(state, D("5"), LimitKind.UPPER_LIMIT, "WHO")
    assert claim.verdict is Verdict.CANNOT_TELL
    low = evaluate_limit(state, D("30"), LimitKind.LOWER_TARGET, "EFSA")
    assert low.verdict is Verdict.CANNOT_TELL


def test_unknown_value_with_trace_qualifier_is_unknown_not_zero():
    items = [
        DayItem("Bread", {Nutrient.SALT: known("2", "2")}),
        DayItem("Olives", {Nutrient.SALT: ItemNutrient(False, None, Qualifier.TRACE)}),
    ]
    state = day_nutrient_state(items, Nutrient.SALT, log_complete=True)
    assert state.recorded == Range(D("2"), D("2"))
    assert "Olives" in state.unresolved
    claim = evaluate_limit(state, D("5"), LimitKind.UPPER_LIMIT, "WHO")
    assert claim.verdict is Verdict.CANNOT_TELL


def test_missing_nutrient_key_is_unresolved():
    items = [DayItem("Mystery bar", {})]
    state = day_nutrient_state(items, Nutrient.FIBRE, log_complete=True)
    assert state.recorded == Range(D("0"), D("0"))
    assert state.unresolved == ("Mystery bar",)
    claim = evaluate_limit(state, D("30"), LimitKind.LOWER_TARGET, "EFSA")
    assert claim.verdict is Verdict.CANNOT_TELL


def test_upper_crossing_threshold_on_complete_day_cannot_tell():
    items = [DayItem("Rice", {Nutrient.SALT: known("4", "6")})]
    state = day_nutrient_state(items, Nutrient.SALT, log_complete=True)
    claim = evaluate_limit(state, D("5"), LimitKind.UPPER_LIMIT, "WHO")
    assert claim.verdict is Verdict.CANNOT_TELL
    assert claim.reasons


def test_reaches_and_below():
    hi = day_nutrient_state(
        [DayItem("Beans", {Nutrient.FIBRE: known("31", "33")})], Nutrient.FIBRE, False
    )
    reach = evaluate_limit(hi, D("30"), LimitKind.LOWER_TARGET, "EFSA")
    assert reach.verdict is Verdict.REACHES
    assert reach.message.startswith("Your recorded values")
    lo = day_nutrient_state(
        [DayItem("Toast", {Nutrient.FIBRE: known("12", "14")})], Nutrient.FIBRE, True
    )
    below = evaluate_limit(lo, D("30"), LimitKind.LOWER_TARGET, "EFSA")
    assert below.verdict is Verdict.BELOW
    assert below.message.startswith("Based on your completed log, ")


def test_period_with_no_complete_days_has_no_mean():
    d = date(2026, 10, 10)
    state = day_nutrient_state([], Nutrient.SALT, log_complete=False)
    summary = period_summary([(d, state)])
    assert summary.included == ()
    assert summary.excluded == (d,)
    assert summary.mean is None
    assert "2026-10-10" in summary.message


def test_trace_reason_says_unquantified():
    items = [
        DayItem("Bread", {Nutrient.SALT: known("2", "2")}),
        DayItem("Cheese", {Nutrient.SALT: known("0", None, Qualifier.TRACE)}),
    ]
    state = day_nutrient_state(items, Nutrient.SALT, log_complete=True)
    assert state.unquantified == ("Cheese",)
    assert state.unresolved == ()
    assert state.fields_complete is False
    claim = evaluate_limit(state, D("5"), LimitKind.UPPER_LIMIT, "WHO")
    assert claim.verdict is Verdict.CANNOT_TELL
    assert "trace (unquantified): Cheese" in claim.reasons
    assert not any("unknown" in r for r in claim.reasons)


def test_cooking_additions_reason():
    items = [
        DayItem(
            "Homemade curry",
            {Nutrient.FAT: known("10", "10")},
            unresolved_for=frozenset({Nutrient.SALT, Nutrient.FAT}),
        ),
        DayItem("Energy drink", {}),
    ]
    state = day_nutrient_state(items, Nutrient.FAT, log_complete=True)
    claim = evaluate_limit(state, D("70"), LimitKind.UPPER_LIMIT, "EFSA")
    assert claim.verdict is Verdict.CANNOT_TELL
    assert "cooking additions not declared: Homemade curry" in claim.reasons
    assert "unknown for: Energy drink" in claim.reasons
    assert not any("unknown" in r and "Homemade curry" in r for r in claim.reasons)
