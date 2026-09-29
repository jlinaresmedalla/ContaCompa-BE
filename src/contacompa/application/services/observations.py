"""Observation report: what the checks found across purchase docs, by code."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contacompa.domain.checks import Severity, has_warnings, severity_of
from contacompa.infrastructure.db.models import PurchaseDoc


@dataclass
class CodeCount:
    code: str
    severity: Severity
    occurrences: int
    documents: int


@dataclass
class ObservationReport:
    documents: int = 0
    clean: int = 0  # no observation at all
    with_warnings: int = 0
    by_code: list[CodeCount] = field(default_factory=list)


async def observation_report(
    session: AsyncSession, company_id: UUID, date_from: date | None, date_to: date | None
) -> ObservationReport:
    stmt = select(PurchaseDoc.issues).where(PurchaseDoc.company_id == company_id)
    if date_from is not None:
        stmt = stmt.where(PurchaseDoc.issue_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(PurchaseDoc.issue_date <= date_to)
    report = ObservationReport()
    occurrences: Counter[str] = Counter()
    documents: Counter[str] = Counter()
    for issues in (await session.execute(stmt)).scalars():
        report.documents += 1
        if not issues:
            report.clean += 1
        if has_warnings(issues):
            report.with_warnings += 1
        codes = [str(issue.get("code")) for issue in issues]
        occurrences.update(codes)
        documents.update(set(codes))
    report.by_code = [
        CodeCount(code, severity_of(code), count, documents[code])
        for code, count in occurrences.most_common()
    ]
    return report
