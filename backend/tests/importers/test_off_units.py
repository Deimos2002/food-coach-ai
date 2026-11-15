from decimal import Decimal

from app.domain.values import Derivation, Nutrient
from app.importers.off import off_values


def test_off_caffeine_grams_are_converted_to_the_apps_milligrams():
    values = off_values({"caffeine_100g": "0.032", "sugars_100g": "11.123456789012345678"})
    assert values[Nutrient.CAFFEINE].range.lower == Decimal(32)
    assert values[Nutrient.CAFFEINE].derivation == Derivation.CONVERTED
    assert values[Nutrient.SUGARS].range.lower == Decimal("11.123456789012345678")


def test_off_alcohol_percentage_is_not_misrepresented_as_grams():
    assert not off_values({"alcohol_100g": "5"})[Nutrient.ALCOHOL].known
    assert off_values({"alcohol_100g": "0"})[Nutrient.ALCOHOL].range.lower == Decimal(0)
    assert not off_values({})[Nutrient.ALCOHOL].known
    assert not off_values({})[Nutrient.CAFFEINE].known
