"""Prices with and without IGV (Peru's 18% sales tax).

The printed value is never changed. The other column is derived from it with the purchase doc's
`prices_include_igv` flag: divided by 1.18 when the printed price includes IGV, multiplied by 1.18
when it does not (ADR 0008). Without a flag there is nothing to derive from."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

IGV_RATE = Decimal("0.18")
IGV_FACTOR = Decimal(1) + IGV_RATE
CENTS = Decimal("0.01")
UNIT_PRICE_PLACES = Decimal("0.0001")  # suppliers print up to four decimals


@dataclass(frozen=True)
class Priced:
    without_igv: Decimal | None
    with_igv: Decimal | None


def both_prices(value: Decimal | None, includes_igv: bool | None, places: Decimal) -> Priced:
    """The printed value in its own column and the derived one in the other."""
    if value is None or includes_igv is None:
        return Priced(None, None)
    if includes_igv:
        return Priced(_round(value / IGV_FACTOR, places), value)
    return Priced(value, _round(value * IGV_FACTOR, places))


@dataclass(frozen=True)
class DocTotals:
    taxable: Decimal | None  # base imponible, without IGV
    igv: Decimal | None
    total: Decimal | None  # importe total, with IGV


def doc_totals(total_amount: Decimal | None) -> DocTotals:
    """The printed importe total always includes IGV, whatever the unit prices show."""
    if total_amount is None:
        return DocTotals(None, None, None)
    taxable = _round(total_amount / IGV_FACTOR, CENTS)
    return DocTotals(taxable, total_amount - taxable, total_amount)


def _round(value: Decimal, places: Decimal) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)
