from decimal import Decimal

import pytest

from app.domain.ciqual_parse import parse_ciqual_cell
from app.domain.values import Qualifier, Source


def test_simple_decimal_comma():
    result = parse_ciqual_cell("12,5")
    assert result.known is True
    assert result.range.lower == Decimal("12.5")
    assert result.range.upper == Decimal("12.5")
    assert result.qualifier == Qualifier.NONE
    assert result.source == Source.CIQUAL


def test_float_input():
    result = parse_ciqual_cell(12.5)
    assert result.known is True
    assert result.range.lower == Decimal("12.5")
    assert result.range.upper == Decimal("12.5")
    assert result.qualifier == Qualifier.NONE


@pytest.mark.parametrize("raw", ["< 0,5", "<0,5"])
def test_less_than(raw):
    result = parse_ciqual_cell(raw)
    assert result.known is True
    assert result.range.lower == Decimal(0)
    assert result.range.upper == Decimal("0.5")
    assert result.qualifier == Qualifier.LESS_THAN


@pytest.mark.parametrize("raw", ["traces", "Traces"])
def test_traces(raw):
    result = parse_ciqual_cell(raw)
    assert result.known is True
    assert result.range.lower == Decimal(0)
    assert result.range.upper is None
    assert result.qualifier == Qualifier.TRACE


@pytest.mark.parametrize("raw", ["-", "", None])
def test_unknown(raw):
    result = parse_ciqual_cell(raw)
    assert result.known is False
    assert result.range is None


def test_zero():
    result = parse_ciqual_cell("0")
    assert result.known is True
    assert result.range.lower == Decimal(0)
    assert result.range.upper == Decimal(0)


def test_garbage_raises():
    with pytest.raises(ValueError):
        parse_ciqual_cell("abc")


@pytest.mark.parametrize("raw", ["-5", "-0,3", "< -1"])
def test_negative_value_raises(raw):
    with pytest.raises(ValueError):
        parse_ciqual_cell(raw)


def test_nbsp_padded_point_value():
    result = parse_ciqual_cell("\xa012,5\xa0")
    assert result.known is True
    assert result.range.lower == Decimal("12.5")
    assert result.range.upper == Decimal("12.5")
    assert result.qualifier == Qualifier.NONE


def test_nbsp_padded_less_than():
    result = parse_ciqual_cell("<\xa00,5")
    assert result.known is True
    assert result.range.lower == Decimal(0)
    assert result.range.upper == Decimal("0.5")
    assert result.qualifier == Qualifier.LESS_THAN


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), "NaN", "Infinity", "-inf"])
def test_non_finite_cell_raises_value_error(raw):
    # A ValueError (not InvalidOperation) goes down the importer's unknown-and-count path.
    with pytest.raises(ValueError):
        parse_ciqual_cell(raw)
