"""Meal-item quantities and unit-safe conversion to a product's basis unit.

g <-> ml is only allowed with a density (g per ml); without one the amount is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Unit(StrEnum):
    G = "g"
    ML = "ml"


class QuantityKind(StrEnum):
    RECORDED = "recorded"
    ESTIMATED_RANGE = "estimated_range"


class QuantitySource(StrEnum):
    WEIGHED = "weighed"
    PACKAGE = "package"
    LABEL_PORTION = "label_portion"
    USER_ESTIMATE = "user_estimate"
    PHOTO_ESTIMATE = "photo_estimate"


@dataclass(frozen=True)
class Quantity:
    kind: QuantityKind
    value: Decimal
    lower: Decimal
    upper: Decimal
    unit: Unit
    source: QuantitySource

    def __post_init__(self) -> None:
        for name in ("value", "lower", "upper"):
            if not isinstance(getattr(self, name), Decimal):
                raise TypeError(f"Quantity.{name} must be a Decimal")
            if not getattr(self, name).is_finite():
                raise ValueError(f"Quantity.{name} must be finite")
        if self.lower < 0:
            raise ValueError("quantity cannot be negative")
        if not self.lower <= self.value <= self.upper:
            raise ValueError("quantity must satisfy lower <= value <= upper")
        if self.kind is QuantityKind.RECORDED and not self.lower == self.value == self.upper:
            raise ValueError("a recorded quantity is a point: lower == value == upper")

    @classmethod
    def recorded(cls, v: Decimal, unit: Unit, source: QuantitySource) -> Quantity:
        return cls(QuantityKind.RECORDED, v, v, v, unit, source)

    @classmethod
    def estimated(
        cls, v: Decimal, lower: Decimal, upper: Decimal, unit: Unit, source: QuantitySource
    ) -> Quantity:
        return cls(QuantityKind.ESTIMATED_RANGE, v, lower, upper, unit, source)


class UnitMismatch(ValueError):
    """The amount's unit cannot be converted to the product's basis unit."""


def to_basis_amount(
    q: Quantity, basis_unit: Unit, density_g_per_ml: Decimal | None
) -> tuple[Decimal, Decimal]:
    """Return (lower, upper) of ``q`` expressed in ``basis_unit``."""
    if q.unit == basis_unit:
        return (q.lower, q.upper)
    if density_g_per_ml is None:
        raise UnitMismatch(
            f"amount in {q.unit} cannot be used for a per-100 {basis_unit} product "
            "without a sourced density"
        )
    if not isinstance(density_g_per_ml, Decimal) or density_g_per_ml <= 0:
        raise UnitMismatch("density must be a positive Decimal (g per ml)")
    if q.unit == Unit.G and basis_unit == Unit.ML:
        return (q.lower / density_g_per_ml, q.upper / density_g_per_ml)
    if q.unit == Unit.ML and basis_unit == Unit.G:
        return (q.lower * density_g_per_ml, q.upper * density_g_per_ml)
    raise UnitMismatch(f"unsupported conversion {q.unit} -> {basis_unit}")
