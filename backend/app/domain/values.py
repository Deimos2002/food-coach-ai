"""Nutrient values and ranges.

Unknown is never 0: an unknown value has no range at all, while a known zero is the
explicit point [0, 0] with a source. All arithmetic uses Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Nutrient(StrEnum):
    ENERGY_KCAL = "energy_kcal"
    PROTEIN = "protein"
    CARBOHYDRATE = "carbohydrate"
    SUGARS = "sugars"
    FAT = "fat"
    SATURATED_FAT = "saturated_fat"
    FIBRE = "fibre"
    SALT = "salt"
    CAFFEINE = "caffeine"
    ALCOHOL = "alcohol"
    POLYOLS = "polyols"
    ERYTHRITOL = "erythritol"


@dataclass(frozen=True)
class Range:
    """A closed estimation range. ``upper=None`` means unbounded above (e.g. a trace).

    Bounds are finite and the lower bound is never negative (nutrient amounts and totals).
    """

    lower: Decimal
    upper: Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.lower, Decimal):
            raise TypeError("Range.lower must be a Decimal")
        if not self.lower.is_finite():
            raise ValueError("Range.lower must be finite")
        if self.lower < 0:
            raise ValueError(f"Range lower {self.lower} is negative")
        if self.upper is not None:
            if not isinstance(self.upper, Decimal):
                raise TypeError("Range.upper must be a Decimal or None")
            if not self.upper.is_finite():
                raise ValueError("Range.upper must be finite")
            if self.upper < self.lower:
                raise ValueError(f"Range upper {self.upper} is below lower {self.lower}")

    @classmethod
    def point(cls, v: Decimal) -> Range:
        return cls(v, v)

    def __add__(self, other: Range) -> Range:
        if not isinstance(other, Range):
            return NotImplemented
        upper = None if self.upper is None or other.upper is None else self.upper + other.upper
        return Range(self.lower + other.lower, upper)

    def scale(self, lo: Decimal, hi: Decimal) -> Range:
        """Multiply the lower end by ``lo`` and the upper end by ``hi``; None stays None."""
        return Range(self.lower * lo, None if self.upper is None else self.upper * hi)


class Qualifier(StrEnum):
    NONE = "none"
    TRACE = "trace"
    LESS_THAN = "less_than"


class Source(StrEnum):
    LABEL = "label"
    CIQUAL = "ciqual"
    OFF_CONTRIBUTOR = "off_contributor"
    MANUAL = "manual"


class Derivation(StrEnum):
    DIRECT = "direct"
    CONVERTED = "converted"
    COMPUTED = "computed"


class Verification(StrEnum):
    UNVERIFIED = "unverified"
    USER_VERIFIED = "user_verified"
    FLAGGED = "flagged"


@dataclass(frozen=True)
class NutrientValue:
    """A per-100 (g or ml) nutrient value. Known values carry a range; unknown ones do not."""

    known: bool
    range: Range | None
    qualifier: Qualifier
    source: Source
    derivation: Derivation = Derivation.DIRECT
    verification: Verification = Verification.UNVERIFIED

    def __post_init__(self) -> None:
        if self.known and self.range is None:
            raise ValueError("a known NutrientValue needs a range")
        if not self.known and self.range is not None:
            raise ValueError("an unknown NutrientValue must not carry a range")

    @classmethod
    def unknown(cls, source: Source) -> NutrientValue:
        return cls(False, None, Qualifier.NONE, source)

    @classmethod
    def point(cls, value: Decimal, source: Source) -> NutrientValue:
        return cls(True, Range.point(value), Qualifier.NONE, source)
