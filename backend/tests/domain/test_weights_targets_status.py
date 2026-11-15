from datetime import date, timedelta
from decimal import Decimal as D

from app.domain.day_status import DayEvent, DayStatus, next_status
from app.domain.targets import Profile, Targets, compute_targets
from app.domain.weights import WeightEntry, compute_trend


def entries(*weights):
    return [WeightEntry(date(2026, 10, 1) + timedelta(days=i), D(w)) for i, w in enumerate(weights)]


def test_a19_typo_first_entry_does_not_poison_trend():
    result = compute_trend(entries("7.5", "72.1", "72.3", "72.0", "72.2", "72.4"))
    assert all(not point.flagged for point in result[1:])
    assert D("72.0") <= result[-1].trend <= D("72.4")


def test_outlier_flagged_after_five():
    result = compute_trend(entries("72", "72", "72", "72", "72", "75.5"))
    assert result[-1].flagged
    assert result[-1].trend == D("72.00")


def test_two_flagged_same_side_suggest_confirm():
    result = compute_trend(entries("72", "72", "72", "72", "72", "75.5", "75.6"))
    assert not result[-2].suggest_confirm
    assert result[-1].flagged
    assert result[-1].suggest_confirm


def profile(pace="0.5"):
    return Profile("male", 1996, D(180), D("1.55"), "lose", D(pace))


def test_targets_male_example():
    assert compute_targets(profile(), D(80), date(2026, 10, 12)) == Targets(2759, 2319, 144)


def test_pace_capped_at_1pct():
    assert compute_targets(profile("2"), D(80), date(2026, 10, 12)) == compute_targets(
        profile("1"), D(80), date(2026, 10, 12)
    )


def test_a17_delete_from_complete_becomes_partial():
    assert next_status(DayStatus.COMPLETE, DayEvent.ITEM_DELETED) == DayStatus.PARTIAL


def test_add_to_complete_stays_complete():
    assert next_status(DayStatus.COMPLETE, DayEvent.ITEM_ADDED) == DayStatus.COMPLETE
