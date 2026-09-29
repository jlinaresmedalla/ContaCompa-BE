"""Response and request models for the purchase doc endpoints (the OpenAPI shape at /docs)."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from contacompa.application.services.costs import CostLine, CostReport
from contacompa.application.services.monitor import JobRow
from contacompa.application.services.observations import ObservationReport
from contacompa.application.services.records import PurchaseDocView
from contacompa.domain.checks import Severity, has_warnings, severity_of
from contacompa.domain.igv import CENTS, UNIT_PRICE_PLACES, both_prices, doc_totals
from contacompa.domain.purchase_doc import DocType, SourceKind
from contacompa.infrastructure.db.models import PurchaseDoc, PurchaseDocLine, Supplier


class MintKeyIn(BaseModel):
    company_ruc: str


class MintedKeyOut(BaseModel):
    """The only time the key is shown; only its hash is stored."""

    api_key: str
    company_ruc: str
    expires_at: datetime


class CompanyOut(BaseModel):
    ruc: str
    legal_name: str


class MeOut(BaseModel):
    company: CompanyOut
    expires_at: datetime


class SupplierOut(BaseModel):
    ruc: str | None
    legal_name: str | None


class IssueOut(BaseModel):
    code: str
    severity: Severity
    detail: str
    field: str | None = None
    line_number: int | None = None


class LineOut(BaseModel):
    """A line as printed, plus the derived column: both prices with and without IGV."""

    id: UUID
    line_number: int
    description: str | None
    quantity: Decimal | None
    unit: str
    unit_price: Decimal | None
    line_total: Decimal | None
    unit_price_without_igv: Decimal | None
    unit_price_with_igv: Decimal | None
    line_total_without_igv: Decimal | None
    line_total_with_igv: Decimal | None

    @classmethod
    def build(cls, line: PurchaseDocLine, includes_igv: bool | None) -> "LineOut":
        price = both_prices(line.unit_price, includes_igv, UNIT_PRICE_PLACES)
        total = both_prices(line.line_total, includes_igv, CENTS)
        return cls(
            id=line.id,
            line_number=line.line_number,
            description=line.description,
            quantity=line.quantity,
            unit=line.unit,
            unit_price=line.unit_price,
            line_total=line.line_total,
            unit_price_without_igv=price.without_igv,
            unit_price_with_igv=price.with_igv,
            line_total_without_igv=total.without_igv,
            line_total_with_igv=total.with_igv,
        )


class PurchaseDocSummary(BaseModel):
    id: UUID
    supplier: SupplierOut | None
    doc_type: DocType
    doc_number: str | None
    issue_date: date | None
    currency: str | None
    total_amount: Decimal | None
    prices_include_igv: bool | None
    taxable_amount: Decimal | None
    igv_amount: Decimal | None
    has_warnings: bool
    issues: list[IssueOut]
    lines: list[LineOut]

    @classmethod
    def build(
        cls, doc: PurchaseDoc, supplier: Supplier | None, lines: list[PurchaseDocLine]
    ) -> "PurchaseDocSummary":
        totals = doc_totals(doc.total_amount)
        return cls(
            id=doc.id,
            supplier=SupplierOut(
                ruc=supplier.ruc if supplier else None, legal_name=doc.supplier_name
            )
            if supplier or doc.supplier_name
            else None,
            doc_type=doc.doc_type,
            doc_number=doc.doc_number,
            issue_date=doc.issue_date,
            currency=doc.currency,
            total_amount=doc.total_amount,
            prices_include_igv=doc.prices_include_igv,
            taxable_amount=totals.taxable,
            igv_amount=totals.igv,
            has_warnings=has_warnings(doc.issues),
            issues=[
                IssueOut.model_validate({**issue, "severity": severity_of(issue.get("code", ""))})
                for issue in doc.issues
            ],
            lines=[LineOut.build(line, doc.prices_include_igv) for line in lines],
        )


class DocumentRef(BaseModel):
    id: UUID
    filename: str
    source_kind: SourceKind


class CorrectionOut(BaseModel):
    field: str
    line_id: UUID | None
    old_value: str | None
    new_value: str | None
    corrected_by: str
    corrected_at: datetime


class PurchaseDocDetail(PurchaseDocSummary):
    buyer_ruc: str | None
    created_at: datetime
    exported_at: datetime | None
    documents: list[DocumentRef]
    corrections: list[CorrectionOut]

    @classmethod
    def from_view(cls, view: PurchaseDocView) -> "PurchaseDocDetail":
        doc = view.doc
        summary = PurchaseDocSummary.build(doc, view.supplier, view.lines)
        return cls(
            **summary.model_dump(),
            buyer_ruc=doc.buyer_ruc,
            created_at=doc.created_at,
            exported_at=doc.exported_at,
            documents=[
                DocumentRef.model_validate(document, from_attributes=True)
                for document in view.documents
            ],
            corrections=[
                CorrectionOut.model_validate(correction, from_attributes=True)
                for correction in view.corrections
            ],
        )


class PurchaseDocList(BaseModel):
    items: list[PurchaseDocSummary]
    next_offset: int | None


class HeaderCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_type: DocType | None = None
    doc_number: str | None = None
    issue_date: date | None = None
    currency: str | None = None
    total_amount: Decimal | None = None
    prices_include_igv: bool | None = None
    buyer_ruc: str | None = None
    supplier_ruc: str | None = None
    supplier_name: str | None = None


class LineCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    description: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None


class CorrectionRequest(BaseModel):
    fields: HeaderCorrection = Field(
        default_factory=HeaderCorrection,
        description='Header values to change, e.g. {"total_amount": "201.00"}. Supplier: '
        "supplier_ruc, supplier_name.",
        examples=[{"total_amount": "201.00"}],
    )
    lines: list[LineCorrection] = Field(
        default_factory=list,
        description="Line values to change; each item needs the line's id.",
        examples=[[{"id": "00000000-0000-0000-0000-000000000000", "quantity": "30"}]],
    )
    reviewer: str = Field(default="accountant", max_length=128)


class CodeCountOut(BaseModel):
    code: str
    severity: Severity
    occurrences: int
    documents: int


class ObservationReportOut(BaseModel):
    documents: int
    clean: int
    with_warnings: int
    by_code: list[CodeCountOut]

    @classmethod
    def build(cls, report: ObservationReport) -> "ObservationReportOut":
        return cls.model_validate(report, from_attributes=True)


class JobRowOut(BaseModel):
    job_id: UUID
    document_id: UUID
    filename: str
    source_kind: str
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    finished_at: datetime | None
    purchase_doc_id: UUID | None
    observations: int
    warnings: bool
    doc_number: str | None

    @classmethod
    def build(cls, row: JobRow) -> "JobRowOut":
        return cls.model_validate(row, from_attributes=True)


class MonitorOut(BaseModel):
    counts: dict[str, int]
    jobs: list[JobRowOut]


class CostLineOut(BaseModel):
    docs: int
    input_tokens: int
    output_tokens: int
    billed_usd: Decimal
    list_usd: Decimal
    list_usd_per_doc: Decimal

    @classmethod
    def build(cls, line: CostLine) -> "CostLineOut":
        return cls(
            docs=line.docs,
            input_tokens=line.input_tokens,
            output_tokens=line.output_tokens,
            billed_usd=line.billed_usd,
            list_usd=line.list_usd,
            list_usd_per_doc=line.list_usd_per_doc,
        )


class ModelCostOut(CostLineOut):
    model: str


class DayCostOut(CostLineOut):
    day: date


class CostReportOut(BaseModel):
    today: CostLineOut
    month: CostLineOut
    total: CostLineOut
    by_model: list[ModelCostOut]
    by_day: list[DayCostOut]

    @classmethod
    def build(cls, report: CostReport) -> "CostReportOut":
        return cls(
            today=CostLineOut.build(report.today),
            month=CostLineOut.build(report.month),
            total=CostLineOut.build(report.total),
            by_model=[
                ModelCostOut(model=name, **CostLineOut.build(line).model_dump())
                for name, line in sorted(report.by_model.items())
            ],
            by_day=[
                DayCostOut(day=day, **CostLineOut.build(line).model_dump())
                for day, line in sorted(report.by_day.items())
            ],
        )
