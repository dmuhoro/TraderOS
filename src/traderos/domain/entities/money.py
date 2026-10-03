"""Exact money conversion between `Decimal` and SQLite scaled integers.

ADR-009. Two rules govern this module, and both exist because of a specific
failure mode rather than a general preference:

1. **Never `Decimal(float_value)`.** It captures the double's error and then
   presents it as exact: `Decimal(0.1)` is `0.1000000000000000055511151231…`,
   permanently. Any value arriving from a legacy `REAL` column must come through
   `Decimal(str(value))`, which reads the shortest decimal that round-trips.
2. **Never rely on the decimal context for rounding.** `ROUND_HALF_EVEN` is
   named at every conversion so a global context change cannot silently alter
   what gets written to the ledger.

Quantities and prices share a scale of 8 decimal places (see ADR-009,
"Rejected alternatives", for why 8 and not 18). Conversion raises rather than
truncating when a value needs more precision than the scale allows, because a
silently truncated quantity is an order for the wrong size.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN
from decimal import Decimal
from decimal import InvalidOperation

PRICE_SCALE = 8
QTY_SCALE = 8

_PRICE_EXP = Decimal(1).scaleb(-PRICE_SCALE)
_QTY_EXP = Decimal(1).scaleb(-QTY_SCALE)

#: The precision unit each scale represents, for callers that need to reason
#: about a stored integer (e.g. formatting a report).
PRICE_UNIT = _PRICE_EXP
QTY_UNIT = _QTY_EXP


class MoneyPrecisionError(ValueError):
    """A value cannot be represented exactly at the column's scale.

    Raised instead of rounding, because rounding a quantity changes the size of
    an order and rounding a price changes the price paid.
    """


def _as_decimal(value: Decimal | int | str) -> Decimal:
    """Coerce to `Decimal`, refusing `float`.

    A `float` reaching this function is a caller still holding binary money, and
    the point of ADR-009 is that it should fail where it is visible rather than
    propagate. `MoneyPrecisionError` names the actual mistake; `TypeError` would
    be true but unhelpful.
    """
    if isinstance(value, float):
        raise MoneyPrecisionError(
            f"refusing to convert float {value!r} to money; binary floats are not "
            "exact. Use Decimal(str(value)) for legacy data, or fix the caller to "
            "carry Decimal end to end (ADR-009)."
        )
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise MoneyPrecisionError(f"{value!r} is not a valid decimal amount") from exc


def to_minor_units(
    value: Decimal | int | str,
    scale: int = PRICE_SCALE,
    *,
    exact: bool = True,
) -> int:
    """Convert a decimal amount to its scaled integer representation.

    Args:
        value: The amount. `float` is refused; see `_as_decimal`.
        scale: Decimal places to preserve.
        exact: When `True` (the default) a value needing more precision raises.
            `False` rounds half-even and exists only for migrating legacy data,
            where the alternative is refusing to read the row at all.
    """
    amount = _as_decimal(value)
    scaled = amount.scaleb(scale)
    integral = scaled.to_integral_value(rounding=ROUND_HALF_EVEN)
    if exact and scaled != integral:
        raise MoneyPrecisionError(
            f"{amount} needs more than {scale} decimal places; refusing to round "
            f"it to {integral.scaleb(-scale)}. Pass exact=False to migrate legacy data."
        )
    return int(integral)


def from_minor_units(value: int, scale: int = PRICE_SCALE) -> Decimal:
    """Convert a scaled integer back to a `Decimal` amount.

    Accepts `int` only. A `float` here means a stored integer was read through a
    float-typed ORM or JSON path, which is the exact loss ADR-009 forbids.
    """
    if isinstance(value, float):
        raise MoneyPrecisionError(
            f"refusing to read money from float {value!r}; scaled integers must "
            "stay int (ADR-009)."
        )
    return Decimal(value).scaleb(-scale)


def to_price_minor_units(value: Decimal | int | str, *, exact: bool = True) -> int:
    return to_minor_units(value, PRICE_SCALE, exact=exact)


def from_price_minor_units(value: int) -> Decimal:
    return from_minor_units(value, PRICE_SCALE)


def to_qty_minor_units(value: Decimal | int | str, *, exact: bool = True) -> int:
    return to_minor_units(value, QTY_SCALE, exact=exact)


def from_qty_minor_units(value: int) -> Decimal:
    return from_minor_units(value, QTY_SCALE)


def from_legacy_real(value: float, scale: int = PRICE_SCALE) -> int:
    """Convert a value read from a legacy `REAL` column to scaled units.

    The `str()` hop is the whole point. `Decimal(0.1)` is
    `0.1000000000000000055511151231257827…`; `Decimal(str(0.1))` is exactly
    `0.1`. A migration that used the former would write the double's error into
    a column it then describes as exact — the ledger would be wrong forever and
    confident about it.

    `exact=False` here by necessity: legacy rows may hold values with more
    precision than the scale keeps, and the migration's job is to report that
    loss, not to refuse the row.
    """
    return to_minor_units(Decimal(str(value)), scale, exact=False)
