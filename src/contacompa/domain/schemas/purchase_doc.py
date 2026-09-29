from pydantic import BaseModel, Field

from contacompa.domain.fields import ExtractedField, FieldKind


class PurchaseDocLine(BaseModel):
    description: ExtractedField
    quantity: ExtractedField
    unit: ExtractedField
    unit_price: ExtractedField
    line_total: ExtractedField


class PurchaseDoc(BaseModel):
    doc_type: str = Field(
        description="invoice (factura), sales_receipt (boleta), sales_note (nota de venta), "
        "credit_note (nota de crédito) or other"
    )
    supplier_ruc: ExtractedField
    supplier_name: ExtractedField
    doc_number: ExtractedField
    issue_date: ExtractedField
    currency: ExtractedField
    total_amount: ExtractedField
    prices_include_igv: ExtractedField
    buyer_ruc: ExtractedField
    lines: list[PurchaseDocLine]


PURCHASE_DOC_KINDS: dict[str, FieldKind] = {
    "supplier_ruc": FieldKind.ruc,
    "supplier_name": FieldKind.text,
    "doc_number": FieldKind.text,
    "issue_date": FieldKind.date,
    "currency": FieldKind.currency,
    "total_amount": FieldKind.amount,
    "prices_include_igv": FieldKind.boolean,
    "buyer_ruc": FieldKind.ruc,
    "lines.description": FieldKind.text,
    "lines.quantity": FieldKind.number,
    "lines.unit": FieldKind.unit,
    "lines.unit_price": FieldKind.number,
    "lines.line_total": FieldKind.amount,
}
