"""Purchase doc values as the reviewed layer stores them, built from a raw extraction's fields.

Pure: no database, no I/O. Terms follow docs/CONTEXT.md."""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class DocType(StrEnum):
    INVOICE = "invoice"
    SALES_RECEIPT = "sales_receipt"
    SALES_NOTE = "sales_note"
    CREDIT_NOTE = "credit_note"
    OTHER = "other"


class SourceKind(StrEnum):
    PDF_TEXT = "pdf_text"
    PDF_SCANNED = "pdf_scanned"
    PHOTO = "photo"


DEFAULT_UNIT = "unit"
MAX_UNIT_LENGTH = 32

_DOC_TYPE_ALIASES = {
    "factura": DocType.INVOICE,
    "boleta": DocType.SALES_RECEIPT,
    "receipt": DocType.SALES_RECEIPT,
    "nota de venta": DocType.SALES_NOTE,
    "nota de credito": DocType.CREDIT_NOTE,
    "nota de crédito": DocType.CREDIT_NOTE,
}

# Printed labels in front of the number: "Nº", "N°", "No.", "Nro.", "Número", "#".
_LABEL = re.compile(r"^(N[º°O]\.?|NRO\.?|N[ÚU]MERO|#)\s*[:.]?\s*")
_DASH = re.compile(r"\s*[-\u2013]\s*")
MAX_DOC_NUMBER_LENGTH = 40


def parse_doc_type(raw: str | None) -> DocType:
    text = (raw or "").strip().casefold()
    try:
        return DocType(text)
    except ValueError:
        return _DOC_TYPE_ALIASES.get(text, DocType.OTHER)


def normalize_doc_number(raw: str | None) -> str | None:
    """The printed document number as one value, without labels or stray spaces:
    'Nº FF01 - 00237450' → 'FF01-00237450'. Leading zeros stay: two photos of the same paper
    print the same number, so they still produce the same value."""
    if not raw:
        return None
    text = " ".join(raw.upper().split())
    previous = None
    while previous != text:
        previous, text = text, _LABEL.sub("", text).strip()
    text = _DASH.sub("-", text)
    return text[:MAX_DOC_NUMBER_LENGTH] or None


@dataclass(frozen=True)
class LineValues:
    line_number: int
    description: str | None
    quantity: Decimal | None
    unit: str
    unit_price: Decimal | None
    line_total: Decimal | None


@dataclass(frozen=True)
class PurchaseDocValues:
    doc_type: DocType
    supplier_ruc: str | None
    supplier_name: str | None
    doc_number: str | None
    issue_date: date | None
    currency: str | None
    total_amount: Decimal | None
    prices_include_igv: bool | None
    buyer_ruc: str | None
    lines: tuple[LineValues, ...] = field(default=())

    @property
    def business_key(self) -> tuple[str, DocType, str] | None:
        """(supplier RUC, doc type, document number), or None when any part is missing."""
        if self.supplier_ruc and self.doc_number:
            return self.supplier_ruc, self.doc_type, self.doc_number
        return None


def values_from_fields(doc_type: str | None, fields: dict[str, Any]) -> PurchaseDocValues:
    """Map a raw extraction's rendered fields (normalized strings) to typed values. Nothing is
    derived: a value the document did not print stays None, except the unit's default."""
    lines = tuple(
        LineValues(
            line_number=index,
            description=_text(row.get("description")),
            quantity=_decimal(row.get("quantity")),
            unit=(_text(row.get("unit")) or DEFAULT_UNIT)[:MAX_UNIT_LENGTH],
            unit_price=_decimal(row.get("unit_price")),
            line_total=_decimal(row.get("line_total")),
        )
        for index, row in enumerate(fields.get("lines") or [], start=1)
    )
    flag = _text(fields.get("prices_include_igv"))
    currency = _text(fields.get("currency"))
    return PurchaseDocValues(
        doc_type=parse_doc_type(doc_type),
        supplier_ruc=_ruc(fields.get("supplier_ruc")),
        supplier_name=_text(fields.get("supplier_name")),
        doc_number=normalize_doc_number(_raw(fields.get("doc_number"))),
        issue_date=_date(fields.get("issue_date")),
        currency=currency if currency and re.fullmatch(r"[A-Z]{3}", currency) else None,
        total_amount=_decimal(fields.get("total_amount")),
        prices_include_igv={"true": True, "false": False}.get(flag or ""),
        buyer_ruc=_ruc(fields.get("buyer_ruc")),
        lines=lines,
    )


def _value(rendered: Any) -> Any:
    return rendered.get("value") if isinstance(rendered, dict) else None


def _raw(rendered: Any) -> str | None:
    if not isinstance(rendered, dict):
        return None
    raw = rendered.get("raw") or rendered.get("value")
    return str(raw) if raw is not None else None


def _ruc(rendered: Any) -> str | None:
    """Only an 11-digit value is a RUC; anything else the model returned is treated as missing."""
    value = _text(rendered)
    return value if value and len(value) == 11 and value.isdigit() else None


def _text(rendered: Any) -> str | None:
    value = _value(rendered)
    return str(value) if value not in (None, "") else None


def _decimal(rendered: Any) -> Decimal | None:
    value = _value(rendered)
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _date(rendered: Any) -> date | None:
    value = _value(rendered)
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
