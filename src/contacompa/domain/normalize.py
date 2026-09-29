"""Turn verbatim strings from documents into canonical values.

Amounts → Decimal as a string with 2+ places; dates → ISO 8601; currencies → ISO 4217 codes.
Returns None when the input cannot be interpreted, so callers can keep the raw value and flag it.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from contacompa.domain.fields import FieldKind

_CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "£": "GBP",
    "GBP": "GBP",
    "S/": "PEN",
    "S/.": "PEN",
    "PEN": "PEN",
    "SOLES": "PEN",
    "SOL": "PEN",
    "NUEVOSSOLES": "PEN",
    "DOLARES": "USD",
    "DÓLARES": "USD",
    "MXN": "MXN",
    "MX$": "MXN",
    "COP": "COP",
    "ARS": "ARS",
    "CLP": "CLP",
    "BRL": "BRL",
    "R$": "BRL",
}

_MONTHS: dict[str, int] = {
    **{
        m: i
        for i, m in enumerate(
            ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
        )
    },
    **{
        m: i
        for i, m in enumerate(
            ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"], 1
        )
    },
    "sept": 9,
    "set": 9,
}

_AMOUNT_RE = re.compile(r"[-+]?\d[\d.,\s']*\d|[-+]?\d")


def normalize_amount(raw: str, decimal: str | None = None) -> str | None:
    """'1.234,56' → '1234.56'; '1,234.56' → '1234.56'; '$ 1 234' → '1234'; '(12.50)' → '-12.50'.
    `decimal` is the document's usual decimal separator; it settles '30.000' (Peru: 30)."""
    value, has_fraction = _parse_decimal(raw, decimal)
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01")) if has_fraction else value, "f")


def normalize_number(raw: str, decimal: str | None = None) -> str | None:
    """Like an amount, but keeps every printed decimal: quantities and unit prices
    ('6355.2967', '186.441') must not be rounded to cents."""
    value, _ = _parse_decimal(raw, decimal)
    return None if value is None else format(value, "f")


def _parse_decimal(raw: str, decimal: str | None) -> tuple[Decimal | None, bool]:
    text = raw.strip()
    negative = (text.startswith("(") and text.endswith(")")) or text.startswith("-")
    match = _AMOUNT_RE.search(text)
    if not match:
        return None, False
    digits = match.group(0).replace(" ", "").replace("'", "").lstrip("+-")
    decimal_sep = _decimal_separator(digits, decimal)
    if decimal_sep is None:
        integer = re.sub(r"[.,]", "", digits)
        fraction = ""
    else:
        integer_part, fraction = digits.rsplit(decimal_sep, 1)
        integer = re.sub(r"[.,]", "", integer_part)
    try:
        value = Decimal(f"{integer}.{fraction}" if fraction else integer)
    except InvalidOperation:
        return None, False
    return (-value if negative else value), bool(fraction)


def _decimal_separator(digits: str, hint: str | None = None) -> str | None:
    """Which of ',' or '.' is the decimal separator, or None when the number is integral.
    A single trailing group of exactly three digits after the only separator is ambiguous: with
    a hint it is decimal when the separator is the hint; without one it is read as a thousands
    group ('1,234' → 1234), anything else as decimals ('12,5' → 12.5)."""
    if "," in digits and "." in digits:
        return "," if digits.rfind(",") > digits.rfind(".") else "."
    for sep in (",", "."):
        if sep in digits:
            if digits.count(sep) > 1:
                return None
            if len(digits.rsplit(sep, 1)[1]) == 3:
                return sep if hint == sep else None
            return sep
    return None


def normalize_integer(raw: str) -> str | None:
    digits = re.sub(r"[^\d-]", "", raw)
    return digits if digits and digits.lstrip("-").isdigit() else None


_DATE_PATTERNS = (
    ("%Y-%m-%d", True),
    ("%d/%m/%Y", False),
    ("%d-%m-%Y", False),
    ("%d.%m.%Y", False),
    ("%m/%d/%Y", False),
    ("%Y/%m/%d", True),
    ("%d/%m/%y", False),
)


_TRAILING_TIME = re.compile(r"\s+\d{1,2}:\d{2}(:\d{2})?(\s*[ap]\.?m\.?)?$")


def normalize_date(raw: str) -> str | None:
    """Accepts ISO, day-first numeric (Spanish/European default), month-first when unambiguous,
    and textual months in English or Spanish ('15 de marzo de 2026', 'March 15, 2026')."""
    text = raw.strip().lower()
    text = _TRAILING_TIME.sub("", text)  # '17/09/2026 11:37:58' → '17/09/2026'
    text = re.sub(r"\b(de|del|of)\b", " ", text)
    text = re.sub(r"[,]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for pattern, _ in _DATE_PATTERNS:
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    textual = re.match(r"^(?:(\d{1,2})\s+)?([a-záéíóú]+)\.?\s+(\d{1,2})?\s*(\d{4})$", text)
    if textual:
        day_first, month_name, day_second, year = textual.groups()
        month = _MONTHS.get(month_name[:4] if month_name.startswith("sept") else month_name[:3])
        day = day_first or day_second
        if month and day:
            try:
                return date(int(year), month, int(day)).isoformat()
            except ValueError:
                return None
    return None


def normalize_datetime(raw: str) -> str | None:
    text = raw.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    day = normalize_date(text)
    return f"{day}T00:00:00" if day else None


def normalize_currency(raw: str) -> str | None:
    text = raw.strip().upper().replace(" ", "")
    if text in _CURRENCY_SYMBOLS:
        return _CURRENCY_SYMBOLS[text]
    if re.fullmatch(r"[A-Z]{3}", text):
        return text
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol.upper() in text:
            return code
    return None


_TRUE = {"true", "yes", "si", "sí", "1"}
_FALSE = {"false", "no", "0"}


def normalize_boolean(raw: str) -> str | None:
    text = raw.strip().casefold()
    if text in _TRUE:
        return "true"
    if text in _FALSE:
        return "false"
    return None


def normalize_ruc(raw: str) -> str | None:
    """Digits only; None unless exactly 11 remain. The check digit is validated separately so a
    misread RUC is still stored as printed and flagged."""
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 11 else None


_UNIT_SYNONYMS = {"niu", "unida", "unidad", "unidades", "und", "und.", "un", "u", "unit", "zz"}


def normalize_unit(raw: str) -> str:
    """Printed forms of 'unit' ('NIU', 'UNIDA', 'U (Bienes)') → 'unit'; other units as printed."""
    text = " ".join(raw.split())
    head = re.sub(r"\s*\(.*\)$", "", text).casefold()
    return "unit" if not head or head in _UNIT_SYNONYMS else text


def normalize(kind: FieldKind, raw: str, decimal: str | None = None) -> str | None:
    match kind:
        case FieldKind.amount:
            return normalize_amount(raw, decimal)
        case FieldKind.number:
            return normalize_number(raw, decimal)
        case FieldKind.boolean:
            return normalize_boolean(raw)
        case FieldKind.ruc:
            return normalize_ruc(raw)
        case FieldKind.unit:
            return normalize_unit(raw)
        case FieldKind.integer:
            return normalize_integer(raw)
        case FieldKind.date:
            return normalize_date(raw)
        case FieldKind.datetime:
            return normalize_datetime(raw)
        case FieldKind.currency:
            return normalize_currency(raw)
        case FieldKind.text:
            return " ".join(raw.split()) or None
