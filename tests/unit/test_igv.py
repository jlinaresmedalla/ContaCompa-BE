"""Both prices from one printed value, on the real sample's numbers."""

from decimal import Decimal

from contacompa.domain.igv import (
    CENTS,
    UNIT_PRICE_PLACES,
    both_prices,
    doc_totals,
)


def test_printed_price_includes_igv() -> None:
    # FKL: 2 x 45.000 = 90.00 with IGV
    price = both_prices(Decimal("45.000"), True, UNIT_PRICE_PLACES)
    assert price.with_igv == Decimal("45.000")  # the printed value, untouched
    assert price.without_igv == Decimal("38.1356")
    total = both_prices(Decimal("90.00"), True, CENTS)
    assert (total.without_igv, total.with_igv) == (Decimal("76.27"), Decimal("90.00"))


def test_printed_price_excludes_igv() -> None:
    # voestalpine: valor unitario 27.58 → precio 32.54
    price = both_prices(Decimal("27.58"), False, UNIT_PRICE_PLACES)
    assert price.without_igv == Decimal("27.58")
    assert price.with_igv == Decimal("32.5444")
    assert both_prices(Decimal("27.58"), False, CENTS).with_igv == Decimal("32.54")


def test_unknown_flag_or_missing_value_derives_nothing() -> None:
    assert both_prices(Decimal("10"), None, CENTS).with_igv is None
    assert both_prices(None, True, CENTS).without_igv is None


def test_doc_totals_split_the_printed_total() -> None:
    totals = doc_totals(Decimal("306.00"))  # FKL: gravada 259.32, IGV 46.68
    assert (totals.taxable, totals.igv, totals.total) == (
        Decimal("259.32"),
        Decimal("46.68"),
        Decimal("306.00"),
    )
    assert doc_totals(None).taxable is None
