"""Parsing of raw Ciqual 2025 table cells into domain ``NutrientValue`` objects.

Pure domain logic: no I/O. Unknown is never 0 — an empty/placeholder cell yields
``NutrientValue.unknown``, never a zero point. Decimal only, never float.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.values import NutrientValue, Qualifier, Range, Source

_UNKNOWN_MARKERS = {"-", ""}


def _to_decimal(number_text: str) -> Decimal:
    """Convert a French-formatted numeric string (comma decimal separator) to Decimal."""
    normalized = number_text.strip().replace(",", ".")
    try:
        return Decimal(normalized)
    except Exception as exc:
        raise ValueError(f"not a valid Ciqual numeric value: {number_text!r}") from exc


def parse_ciqual_cell(raw: str | float | int | None) -> NutrientValue:  # noqa: PYI041
    """Parse one Ciqual table cell into a ``NutrientValue`` with ``source=CIQUAL``.

    Handles:
      - French decimal commas: "12,5" -> 12.5
      - Numeric floats/ints from xlsx: converted via ``Decimal(str(x))``
      - Less-than qualifiers: "< 0,5" / "<0,5" -> Range(0, 0.5), Qualifier.LESS_THAN
      - Trace qualifiers: "traces" / "Traces" -> Range(0, None), Qualifier.TRACE
        (no invented upper bound per spec Addendum A)
      - Unknown markers: "-", "", None -> known=False, range=None
      - Plain numbers: "0" -> Range(0, 0)

    Raises ``ValueError`` for anything that cannot be interpreted (never silently
    treated as unknown).
    """
    if raw is None:
        return NutrientValue.unknown(Source.CIQUAL)

    if isinstance(raw, bool):
        raise TypeError(f"not a valid Ciqual value: {raw!r}")

    if isinstance(raw, (float, int)):
        value = Decimal(str(raw))
        _reject_negative(value)
        return NutrientValue(True, Range.point(value), Qualifier.NONE, Source.CIQUAL)

    text = raw.strip()

    if text in _UNKNOWN_MARKERS:
        return NutrientValue.unknown(Source.CIQUAL)

    if text.lower() == "traces":
        return NutrientValue(True, Range(Decimal(0), None), Qualifier.TRACE, Source.CIQUAL)

    if text.startswith("<"):
        number_text = text[1:].strip()
        upper = _to_decimal(number_text)
        _reject_negative(upper)
        return NutrientValue(
            True, Range(Decimal(0), upper), Qualifier.LESS_THAN, Source.CIQUAL
        )

    value = _to_decimal(text)
    _reject_negative(value)
    return NutrientValue(True, Range.point(value), Qualifier.NONE, Source.CIQUAL)


def _reject_negative(value: Decimal) -> None:
    if not value.is_finite():
        raise ValueError(f"a Ciqual nutrient value must be finite: {value}")
    if value < 0:
        raise ValueError(f"a per-100g Ciqual nutrient value cannot be negative: {value}")
