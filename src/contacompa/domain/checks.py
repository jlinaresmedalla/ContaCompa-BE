"""Issues found on a purchase doc. Checks compare printed values; they never change them."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from contacompa.domain.igv import IGV_FACTOR
from contacompa.domain.purchase_doc import DocType, PurchaseDocValues
from contacompa.domain.ruc import is_valid_ruc

LINE_TOLERANCE = Decimal("0.02")
TOTAL_TOLERANCE = Decimal("0.01")  # 1% of the total


class IssueCode(StrEnum):
    AMOUNT_MISMATCH = "amount_mismatch"
    TOTAL_MISMATCH = "total_mismatch"
    SUSPICIOUS_QUANTITY = "suspicious_quantity"
    INVALID_RUC = "invalid_ruc"
    WRONG_BUYER = "wrong_buyer"
    NOT_DEDUCTIBLE = "not_deductible"
    DUPLICATE_FILE = "duplicate_file"
    MISSING_FIELD = "missing_field"


class Severity(StrEnum):
    INFO = "info"  # a fact worth knowing (a sales note, a second photo); the values are fine
    WARNING = "warning"  # a value may be wrong; worth a look at the original


SEVERITY: dict[IssueCode, Severity] = {
    IssueCode.NOT_DEDUCTIBLE: Severity.INFO,
    IssueCode.DUPLICATE_FILE: Severity.INFO,
}


def severity_of(code: str) -> Severity:
    try:
        return SEVERITY.get(IssueCode(code), Severity.WARNING)
    except ValueError:
        return Severity.WARNING


def has_warnings(issues: Iterable[Mapping[str, object]]) -> bool:
    """True when any observation suggests a value may be wrong."""
    return any(severity_of(str(issue.get("code"))) is Severity.WARNING for issue in issues)


@dataclass(frozen=True)
class Issue:
    code: IssueCode
    detail: str
    field: str | None = None
    line_number: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "severity": severity_of(self.code.value).value,
            "detail": self.detail,
            "field": self.field,
            "line_number": self.line_number,
        }


REQUIRED = ("supplier_ruc", "doc_number", "total_amount")


def find_issues(doc: PurchaseDocValues, company_ruc: str) -> list[Issue]:
    issues = [
        Issue(IssueCode.MISSING_FIELD, f"{name} not found", field=name)
        for name in REQUIRED
        if getattr(doc, name) is None
    ]
    for name in ("supplier_ruc", "buyer_ruc"):
        ruc = getattr(doc, name)
        if ruc is not None and not is_valid_ruc(ruc):
            issues.append(Issue(IssueCode.INVALID_RUC, f"{ruc} fails the check digit", field=name))
    if doc.buyer_ruc is not None and doc.buyer_ruc != company_ruc:
        issues.append(
            Issue(IssueCode.WRONG_BUYER, f"addressed to {doc.buyer_ruc}", field="buyer_ruc")
        )
    if doc.doc_type is DocType.SALES_NOTE:
        issues.append(
            Issue(IssueCode.NOT_DEDUCTIBLE, "a sales note is not a tax document", field="doc_type")
        )
    issues.extend(_line_issues(doc))
    issues.extend(_total_issues(doc))
    return issues


def _line_issues(doc: PurchaseDocValues) -> list[Issue]:
    issues: list[Issue] = []
    for line in doc.lines:
        q, price, total = line.quantity, line.unit_price, line.line_total
        if q is not None and price is not None and total is not None:
            if abs(q * price - total) > LINE_TOLERANCE:
                issues.append(
                    Issue(
                        IssueCode.AMOUNT_MISMATCH,
                        f"{q} x {price} != {total}",
                        field="line_total",
                        line_number=line.line_number,
                    )
                )
        # Multiplication cannot catch swapped quantity and price columns (0.06 x 30 = 30 x 0.06);
        # a fraction of a countable unit usually means exactly that swap.
        if q is not None and line.unit == "unit" and q != q.to_integral_value():
            issues.append(
                Issue(
                    IssueCode.SUSPICIOUS_QUANTITY,
                    f"{q} of a countable unit; quantity and price may be swapped",
                    field="quantity",
                    line_number=line.line_number,
                )
            )
    return issues


def _total_issues(doc: PurchaseDocValues) -> list[Issue]:
    totals = [line.line_total for line in doc.lines]
    if doc.total_amount is None or not totals or any(t is None for t in totals):
        return []
    lines_sum = sum((t for t in totals if t is not None), Decimal(0))
    # Unknown IGV flag: either reading may be right, so only flag when both disagree.
    candidates = {
        True: [lines_sum],
        False: [lines_sum * IGV_FACTOR],
        None: [lines_sum, lines_sum * IGV_FACTOR],
    }[doc.prices_include_igv]
    limit = abs(doc.total_amount) * TOTAL_TOLERANCE
    if all(abs(expected - doc.total_amount) > limit for expected in candidates):
        return [
            Issue(
                IssueCode.TOTAL_MISMATCH,
                f"lines add up to {lines_sum}, total is {doc.total_amount}",
                field="total_amount",
            )
        ]
    return []
