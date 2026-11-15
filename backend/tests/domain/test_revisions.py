from decimal import Decimal

from app.domain.revisions import (
    Action,
    Override,
    decide_revision,
    nutrition_hash,
    split_overrides,
)
from app.domain.values import Nutrient, NutrientValue, Source


def known(value: str) -> NutrientValue:
    return NutrientValue.point(Decimal(value), Source.LABEL)


def unknown() -> NutrientValue:
    return NutrientValue.unknown(Source.LABEL)


def test_a10_identical_nutrition_noop():
    current = {
        Nutrient.ENERGY_KCAL: known("100"),
        Nutrient.SUGARS: known("5"),
    }
    new = {
        Nutrient.ENERGY_KCAL: known("100"),
        Nutrient.SUGARS: known("5"),
    }
    assert nutrition_hash(current) == nutrition_hash(new)
    decision = decide_revision(current, new)
    assert decision.action == Action.NOOP
    assert decision.reasons == ()


def test_a9_sugar_11_to_0_reviews():
    current = {
        Nutrient.ENERGY_KCAL: known("200"),
        Nutrient.SUGARS: known("11"),
    }
    new = {
        Nutrient.ENERGY_KCAL: known("200"),
        Nutrient.SUGARS: known("0"),
    }
    decision = decide_revision(current, new)
    assert decision.action == Action.REVIEW
    assert decision.reasons == ("sugars: 11→0",)


def test_a29_salt_0_1_to_0_9_reviews():
    current = {Nutrient.SALT: known("0.1")}
    new = {Nutrient.SALT: known("0.9")}
    decision = decide_revision(current, new)
    assert decision.action == Action.REVIEW
    assert decision.reasons == ("salt: 0.1→0.9",)


def test_small_values_below_floor_adopt():
    current = {Nutrient.SALT: known("0.01")}
    new = {Nutrient.SALT: known("0.04")}
    decision = decide_revision(current, new)
    assert decision.action == Action.ADOPT
    assert decision.reasons == ()


def test_a27_known_to_unknown_reviews():
    current = {Nutrient.PROTEIN: known("10")}
    new = {Nutrient.PROTEIN: unknown()}
    decision = decide_revision(current, new)
    assert decision.action == Action.REVIEW
    assert decision.reasons == ("protein: known→unknown",)


def test_unknown_to_known_adopts():
    current = {Nutrient.PROTEIN: unknown()}
    new = {Nutrient.PROTEIN: known("10")}
    decision = decide_revision(current, new)
    assert decision.action == Action.ADOPT
    assert decision.reasons == ()


def test_a28_override_pinned_to_old_revision_needs_reconfirm():
    override = Override(
        id=1,
        version=1,
        nutrient=Nutrient.SALT,
        value=known("0.5"),
        pinned_revision_id=1,
        retain_across_revisions=False,
    )
    applied, needs_reconfirm = split_overrides([override], active_revision_id=2)
    assert applied == []
    assert needs_reconfirm == [override]


def test_zero_to_above_floor_is_review():
    current = {Nutrient.SALT: known("0")}
    new = {Nutrient.SALT: known("1")}
    decision = decide_revision(current, new)
    assert decision.action == Action.REVIEW
    assert decision.reasons == ("salt: 0→1",)


def test_zero_to_below_floor_adopts():
    current = {Nutrient.SALT: known("0")}
    new = {Nutrient.SALT: known("0.04")}
    decision = decide_revision(current, new)
    assert decision.action == Action.ADOPT
    assert decision.reasons == ()


def test_known_nutrient_absent_from_new_reviews():
    current = {Nutrient.PROTEIN: known("10")}
    new: dict[Nutrient, NutrientValue] = {}
    decision = decide_revision(current, new)
    assert decision.action == Action.REVIEW
    assert decision.reasons == ("protein: known→unknown",)


def test_hash_ignores_trailing_zeros():
    a = {Nutrient.SUGARS: known("1.0")}
    b = {Nutrient.SUGARS: known("1.00")}
    assert nutrition_hash(a) == nutrition_hash(b)


def test_override_retained_when_flagged():
    override = Override(
        id=2,
        version=1,
        nutrient=Nutrient.SALT,
        value=known("0.5"),
        pinned_revision_id=1,
        retain_across_revisions=True,
    )
    applied, needs_reconfirm = split_overrides([override], active_revision_id=2)
    assert applied == [override]
    assert needs_reconfirm == []
