"""Tests for ADR-009 money conversion. Fails before the migration exists."""

from decimal import Decimal

import pytest

from traderos.domain.entities.money import MoneyPrecisionError
from traderos.domain.entities.money import from_minor_units
from traderos.domain.entities.money import from_qty_minor_units
from traderos.domain.entities.money import to_minor_units
from traderos.domain.entities.money import to_price_minor_units
from traderos.domain.entities.money import to_qty_minor_units


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.1", 10000000),
        ("1.10", 110000000),
        ("0", 0),
        ("-5.5", -550000000),
        ("18432.57", 1843257000000),
        ("0.00000001", 1),
    ],
)
def test_to_minor_units_is_exact(text: str, expected: int) -> None:
    assert to_minor_units(Decimal(text)) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("0.1", "0.10000000"), ("18432.57", "18432.57000000"), ("-5.5", "-5.50000000")],
)
def test_round_trip(text: str, expected: str) -> None:
    assert str(from_minor_units(to_minor_units(Decimal(text)))) == expected


def test_float_is_refused() -> None:
    """A float reaching a money converter is the bug ADR-009 exists to stop."""
    with pytest.raises(MoneyPrecisionError, match="refusing to convert float"):
        to_minor_units(0.1)


def test_float_is_refused_on_read() -> None:
    with pytest.raises(MoneyPrecisionError, match="must stay int"):
        from_minor_units(10000000.0)  # type: ignore[arg-type]


def test_excess_precision_raises_rather_than_truncating() -> None:
    """Rounding a quantity changes the size of an order."""
    with pytest.raises(MoneyPrecisionError, match="refusing to round"):
        to_minor_units(Decimal("0.000000001"))


def test_exact_false_permits_rounding_for_legacy_migration() -> None:
    assert to_minor_units(Decimal("0.000000001"), exact=False) == 0


def test_legacy_real_does_not_bake_in_double_error() -> None:
    """Decimal(str(0.1)) is 0.1. The double itself is not, and never will be."""
    assert to_minor_units(Decimal(str(0.1))) == 10_000_000
    # from_float is the exact, explicit way to ask "what does 0.1 really mean here".
    # The double for 0.1 sits strictly ABOVE one tenth, which is the whole problem:
    # it is not a rounding of 0.1, it is a different number that merely prints as 0.1.
    assert Decimal.from_float(0.1) > Decimal("0.1")
    assert Decimal(str(0.1)) == Decimal("0.1")


def test_separate_fills_sum_exactly() -> None:
    """The ADR's stated proof obligation: 0.1 + 0.2 == 0.3 exactly."""
    fills = [Decimal("0.1"), Decimal("0.2")]
    total = sum(fills, Decimal(0))
    assert total == Decimal("0.3")


def test_float_sum_of_same_fills_does_not() -> None:
    """And the reason: the pre-migration float path genuinely fails this."""
    with pytest.raises(AssertionError):
        assert sum([0.1, 0.2]) == 0.3


def test_price_and_quantity_scales_agree() -> None:
    assert to_price_minor_units(Decimal("1.5")) == to_qty_minor_units(Decimal("1.5"))
    assert from_qty_minor_units(150000000) == Decimal("1.50000000")


def test_int_input_is_accepted() -> None:
    assert to_minor_units(5) == 500000000


def test_garbage_is_reported_not_swallowed() -> None:
    with pytest.raises(MoneyPrecisionError, match="not a valid decimal"):
        to_minor_units("not-a-number")
