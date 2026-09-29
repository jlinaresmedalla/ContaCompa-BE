"""Domain rules for Peruvian purchase docs, on numbers taken from the real CIMMSA sample."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from contacompa.domain.checks import (
    IssueCode,
    Severity,
    find_issues,
    has_warnings,
    severity_of,
)
from contacompa.domain.normalize import (
    normalize_amount,
    normalize_boolean,
    normalize_currency,
    normalize_date,
    normalize_number,
    normalize_ruc,
    normalize_unit,
)
from contacompa.domain.purchase_doc import (
    DocType,
    LineValues,
    PurchaseDocValues,
    normalize_doc_number,
    parse_doc_type,
    values_from_fields,
)
from contacompa.domain.ruc import is_valid_ruc

COMPANY = "20543306771"


@pytest.mark.parametrize(
    "ruc", ["20543306771", "20602229204", "10464893283", "20100036101", "20611103418"]
)
def test_real_rucs_pass_the_check_digit(ruc: str) -> None:
    assert is_valid_ruc(ruc)


@pytest.mark.parametrize("ruc", ["20543306772", "2054330677", "30543306771", "2054330677A"])
def test_bad_rucs_fail(ruc: str) -> None:
    assert not is_valid_ruc(ruc)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("F001-0026729", "F001-0026729"),
        ("Nº FF01 - 00237450", "FF01-00237450"),
        ("E001-6648", "E001-6648"),
        ("N° FPP1-005019", "FPP1-005019"),
        ("No. : N001-00001922", "N001-00001922"),
        ("f007 \u2013 30032", "F007-30032"),  # en dash
        ("TICKET 000123", "TICKET 000123"),  # formats without a series stay as printed
        ("  ", None),
        (None, None),
    ],
)
def test_doc_number_is_one_printed_value(raw: str | None, expected: str | None) -> None:
    assert normalize_doc_number(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("NIU", "unit"), ("UNIDA", "unit"), ("UNIDAD", "unit"), ("U (Bienes)", "unit"), ("", "unit")],
)
def test_printed_units_mean_unit(raw: str, expected: str) -> None:
    assert normalize_unit(raw) == expected


def test_other_units_stay_as_printed() -> None:
    assert normalize_unit("KG") == "KG"


def test_peruvian_numbers_use_dot_decimals() -> None:
    assert normalize_number("30.000", ".") == "30.000"
    assert normalize_number("186.441", ".") == "186.441"
    assert normalize_number("6355.2967", ".") == "6355.2967"
    assert normalize_amount("2,300.00", ".") == "2300.00"
    assert normalize_amount("S/ 7,499.25", ".") == "7499.25"
    assert normalize_amount("1,234", ".") == "1234"


def test_dates_printed_with_a_time() -> None:
    assert normalize_date("17/09/2026 11:37:58") == "2026-09-17"  # Rupay's F.Emisión
    assert normalize_date("01-09-2026 15:55:15") == "2026-09-01"
    assert normalize_date("21/09/2026") == "2026-09-21"


def test_small_normalizers() -> None:
    assert normalize_ruc("RUC: 20543306771") == COMPANY
    assert normalize_ruc("2054330677") is None
    assert normalize_boolean("Sí") == "true" and normalize_boolean("no") == "false"
    assert normalize_boolean("maybe") is None
    assert normalize_currency("SOL") == "PEN" and normalize_currency("SOLES") == "PEN"


def test_doc_type_aliases() -> None:
    assert parse_doc_type("invoice") is DocType.INVOICE
    assert parse_doc_type("Nota de venta") is DocType.SALES_NOTE
    assert parse_doc_type("guía") is DocType.OTHER
    assert parse_doc_type(None) is DocType.OTHER


def line(
    n: int, qty: str | None, price: str | None, total: str | None, unit: str = "unit"
) -> LineValues:
    def dec(value: str | None) -> Decimal | None:
        return Decimal(value) if value is not None else None

    return LineValues(n, f"item {n}", dec(qty), unit, dec(price), dec(total))


def doc(**overrides: Any) -> PurchaseDocValues:
    base: dict[str, Any] = {
        "doc_type": DocType.INVOICE,
        "supplier_ruc": "20100036101",
        "supplier_name": "voestalpine High Performance Metals del Perú S.A.",
        "doc_number": "FF10-73888",
        "issue_date": date(2026, 9, 1),
        "currency": "PEN",
        "total_amount": Decimal("32.54"),
        "prices_include_igv": False,
        "buyer_ruc": COMPANY,
        "lines": (line(1, "1.00", "27.58", "27.58"),),
    }
    base.update(overrides)
    return PurchaseDocValues(**base)


def codes(values: PurchaseDocValues) -> list[IssueCode]:
    return [issue.code for issue in find_issues(values, COMPANY)]


def test_clean_documents_raise_nothing() -> None:
    assert codes(doc()) == []  # voestalpine: 27.58 ex-IGV x 1.18 = 32.54
    fkl = doc(
        supplier_ruc="20547835400",
        total_amount=Decimal("306.00"),
        prices_include_igv=True,
        lines=(line(1, "2", "45.000", "90.00"), line(2, "6", "36.000", "216.00")),
    )
    assert IssueCode.TOTAL_MISMATCH not in codes(fkl)
    assert IssueCode.AMOUNT_MISMATCH not in codes(fkl)


def test_swapped_columns_are_suspicious_not_mismatched() -> None:
    ticket = doc(
        total_amount=Decimal("1.80"),
        prices_include_igv=True,
        lines=(line(1, "0.06", "30.000", "1.80"),),
    )
    assert codes(ticket) == [IssueCode.SUSPICIOUS_QUANTITY]


def test_line_and_total_mismatches() -> None:
    wrong = doc(
        total_amount=Decimal("500.00"),
        prices_include_igv=True,
        lines=(line(1, "2", "45", "80.00"),),
    )
    assert codes(wrong) == [IssueCode.AMOUNT_MISMATCH, IssueCode.TOTAL_MISMATCH]


def test_unknown_igv_flag_accepts_either_reading() -> None:
    assert codes(doc(prices_include_igv=None)) == []


def test_buyer_ruc_type_and_missing_fields() -> None:
    assert codes(doc(buyer_ruc="20100036101")) == [IssueCode.WRONG_BUYER]
    assert codes(doc(doc_type=DocType.SALES_NOTE)) == [IssueCode.NOT_DEDUCTIBLE]
    assert codes(doc(supplier_ruc="20100036102")) == [IssueCode.INVALID_RUC]
    missing = find_issues(doc(doc_number=None), COMPANY)
    assert [(i.code, i.field) for i in missing] == [(IssueCode.MISSING_FIELD, "doc_number")]


def rendered(value: str | None, raw: str | None = None) -> dict[str, Any]:
    return {"value": value, "raw": raw or value}


def test_values_from_rendered_fields() -> None:
    fields = {
        "supplier_ruc": rendered("20602229204"),
        "supplier_name": rendered("SNOW SHOP S.A.C."),
        "doc_number": rendered("F001-26729", raw="Nº F001-0026729"),
        "issue_date": rendered("2026-08-19"),
        "currency": rendered("PEN"),
        "total_amount": rendered("201.00"),
        "prices_include_igv": rendered("true"),
        "buyer_ruc": rendered("RUC 2054", raw="RUC 2054"),  # unparseable: treated as missing
        "lines": [
            {
                "description": rendered("LUCES LED 0.90M"),
                "quantity": rendered("3"),
                "unit": rendered("unit"),
                "unit_price": rendered("19.00"),
                "line_total": rendered("57.00"),
            }
        ],
    }
    values = values_from_fields("invoice", fields)
    assert values.business_key == ("20602229204", DocType.INVOICE, "F001-0026729")
    assert values.issue_date == date(2026, 8, 19)
    assert values.prices_include_igv is True
    assert values.buyer_ruc is None
    assert values.lines[0].quantity == Decimal("3")
    assert values.lines[0].unit_price == Decimal("19.00")


def test_warnings_versus_info_observations() -> None:
    info = [{"code": "not_deductible"}, {"code": "duplicate_file"}]
    assert not has_warnings([])
    assert not has_warnings(info)  # a sales note is a fact, not a wrong value
    assert has_warnings([*info, {"code": "amount_mismatch"}])
    assert has_warnings([{"code": "something_new"}])  # unknown codes are warnings
    assert severity_of("suspicious_quantity") is Severity.WARNING
