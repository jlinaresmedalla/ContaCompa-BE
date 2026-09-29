"""Cost report from the raw results: what was billed (0 on a free tier) and what the same
tokens cost at list price, per day and per model."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contacompa.infrastructure.db.models import Document, ExtractionResult
from contacompa.infrastructure.observability.cost import Usage, cost_for


@dataclass
class CostLine:
    docs: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    billed_usd: Decimal = Decimal(0)
    list_usd: Decimal = Decimal(0)

    def add(self, other: "CostLine") -> None:
        self.docs += other.docs
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.billed_usd += other.billed_usd
        self.list_usd += other.list_usd

    @property
    def list_usd_per_doc(self) -> Decimal:
        return (
            (self.list_usd / self.docs).quantize(Decimal("0.000001")) if self.docs else Decimal(0)
        )


@dataclass
class CostReport:
    today: CostLine = field(default_factory=CostLine)
    month: CostLine = field(default_factory=CostLine)
    total: CostLine = field(default_factory=CostLine)
    by_model: dict[str, CostLine] = field(default_factory=dict)
    by_day: dict[date, CostLine] = field(default_factory=dict)


async def cost_report(session: AsyncSession, company_id: UUID, days: int = 30) -> CostReport:
    day = cast(ExtractionResult.created_at, Date)
    stmt = (
        select(
            day,
            ExtractionResult.provider,
            ExtractionResult.model,
            func.count(),
            func.sum(ExtractionResult.input_tokens),
            func.sum(ExtractionResult.output_tokens),
            func.sum(ExtractionResult.cost_usd),
        )
        .join(Document, Document.id == ExtractionResult.document_id)
        .where(Document.company_id == company_id)
        .group_by(day, ExtractionResult.provider, ExtractionResult.model)
    )
    today = datetime.now(UTC).date()
    since = today - timedelta(days=days - 1)
    report = CostReport(by_day={since + timedelta(days=i): CostLine() for i in range(days)})
    for when, provider, model, docs, tokens_in, tokens_out, billed in (
        await session.execute(stmt)
    ).all():
        usage = Usage(input_tokens=int(tokens_in or 0), output_tokens=int(tokens_out or 0))
        try:
            list_usd = cost_for(usage, provider, model).list_usd
        except KeyError:  # a model missing from the price table still shows its tokens
            list_usd = Decimal(0)
        line = CostLine(
            int(docs), usage.input_tokens, usage.output_tokens, Decimal(billed or 0), list_usd
        )
        report.total.add(line)
        report.by_model.setdefault(f"{provider}/{model}", CostLine()).add(line)
        if when == today:
            report.today.add(line)
        if (when.year, when.month) == (today.year, today.month):
            report.month.add(line)
        if when in report.by_day:
            report.by_day[when].add(line)
    return report
