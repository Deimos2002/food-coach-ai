from dataclasses import replace
from decimal import Decimal as D

from app.domain.energy_check import EnergyResult, LabelFigures, check_energy
from app.domain.values import Range


def label():
    return LabelFigures(
        energy_kcal=D(188),
        carbohydrate=D(50),
        protein=D(5),
        fat=D(2),
        fibre=D(3),
        polyols=D(20),
        erythritol=D(10),
        alcohol=None,
        label_format="eu",
    )


def test_polyols_and_erythritol_no_double_count():
    result = check_energy(label())
    assert result.computed == Range(D(188), D(188))
    assert result.result == EnergyResult.CONSISTENT


def test_erythritol_undeclared_gives_range():
    result = check_energy(replace(label(), erythritol=None))
    assert result.computed == Range(D(164), D(212))
    assert result.result == EnergyResult.CONSISTENT


def test_missing_fibre_incomplete():
    result = check_energy(replace(label(), fibre=None))
    assert result.result == EnergyResult.INCOMPLETE
    assert result.computed is None
    assert result.reasons


def test_us_format_incomplete():
    result = check_energy(replace(label(), label_format="us"))
    assert result.result == EnergyResult.INCOMPLETE
    assert result.computed is None
    assert result.reasons


def test_far_off_inconsistent():
    assert check_energy(replace(label(), energy_kcal=D(400))).result == EnergyResult.INCONSISTENT
