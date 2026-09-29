import pytest

from contacompa.domain.fields import FieldKind
from contacompa.domain.normalize import (
    normalize,
    normalize_amount,
    normalize_currency,
    normalize_date,
    normalize_datetime,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("1234.56", "1234.56"),
        ("12,5", "12.50"),
        ("1,234", "1234"),
        ("1.234", "1234"),
        ("S/ 1 234,00", "1234.00"),
        ("$ 99", "99"),
        ("(12.50)", "-12.50"),
        ("-7,25", "-7.25"),
        ("1.234.567,89", "1234567.89"),
        ("abc", None),
    ],
)
def test_normalize_amount(raw: str, expected: str | None) -> None:
    assert normalize_amount(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-03-15", "2026-03-15"),
        ("15/03/2026", "2026-03-15"),
        ("15-03-2026", "2026-03-15"),
        ("15 de marzo de 2026", "2026-03-15"),
        ("March 15, 2026", "2026-03-15"),
        ("15 Mar 2026", "2026-03-15"),
        ("1 de septiembre de 2026", "2026-09-01"),
        ("not a date", None),
        ("31/02/2026", None),
    ],
)
def test_normalize_date(raw: str, expected: str | None) -> None:
    assert normalize_date(raw) == expected


def test_normalize_datetime() -> None:
    assert normalize_datetime("2026-03-15 14:30") == "2026-03-15T14:30:00"
    assert normalize_datetime("15/03/2026 14:30:05") == "2026-03-15T14:30:05"
    assert normalize_datetime("15/03/2026") == "2026-03-15T00:00:00"
    assert normalize_datetime("??") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$", "USD"),
        ("usd", "USD"),
        ("S/.", "PEN"),
        ("Soles", "PEN"),
        ("€", "EUR"),
        ("XYZ", "XYZ"),
        ("??", None),
    ],
)
def test_normalize_currency(raw: str, expected: str | None) -> None:
    assert normalize_currency(raw) == expected


def test_normalize_dispatch() -> None:
    assert normalize(FieldKind.text, "  Acme   Tools ") == "Acme Tools"
    assert normalize(FieldKind.integer, "No. 42") == "42"
    assert normalize(FieldKind.amount, "1.234,56") == "1234.56"
