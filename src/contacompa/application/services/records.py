"""Purchase doc records: list with observation filters, read, correct, delete. Each call owns
its transaction. There is no approval step (ADR 0007)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contacompa.application.services.purchase_docs import upsert_supplier
from contacompa.domain.checks import IssueCode, Severity, find_issues, severity_of
from contacompa.domain.normalize import normalize_unit
from contacompa.domain.purchase_doc import (
    DocType,
    LineValues,
    PurchaseDocValues,
    normalize_doc_number,
)
from contacompa.infrastructure.blob import BlobStore
from contacompa.infrastructure.db.models import (
    Company,
    Correction,
    Document,
    ExtractionResult,
    Job,
    PurchaseDoc,
    PurchaseDocLine,
    Supplier,
)

ObservationFilter = Literal["all", "any", "warning", "none"]


class NotFoundError(LookupError):
    pass


class InvalidCorrectionError(ValueError):
    pass


class CorrectionConflictError(ValueError):
    pass


@dataclass
class PurchaseDocView:
    doc: PurchaseDoc
    supplier: Supplier | None
    lines: list[PurchaseDocLine]
    documents: list[Document]
    corrections: list[Correction]


HEADER_FIELDS = {
    "doc_type",
    "doc_number",
    "issue_date",
    "currency",
    "total_amount",
    "prices_include_igv",
    "buyer_ruc",
    "supplier_ruc",
    "supplier_name",
}
LINE_FIELDS = {"description", "quantity", "unit", "unit_price", "line_total"}
HEADER_ORDER = (
    "supplier_ruc",
    "supplier_name",
    "doc_type",
    "doc_number",
    "issue_date",
    "currency",
    "total_amount",
    "prices_include_igv",
    "buyer_ruc",
)
LINE_ORDER = ("description", "quantity", "unit", "unit_price", "line_total")


async def list_purchase_docs(
    session: AsyncSession,
    company_id: UUID,
    *,
    observations: ObservationFilter = "all",
    code: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[tuple[PurchaseDoc, Supplier | None]]:
    stmt = (
        select(PurchaseDoc, Supplier)
        .outerjoin(Supplier, Supplier.id == PurchaseDoc.supplier_id)
        .where(PurchaseDoc.company_id == company_id)
        .order_by(PurchaseDoc.issue_date.desc().nulls_last(), PurchaseDoc.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if observations == "none":
        stmt = stmt.where(func.jsonb_array_length(PurchaseDoc.issues) == 0)
    elif observations == "any":
        stmt = stmt.where(func.jsonb_array_length(PurchaseDoc.issues) > 0)
    elif observations == "warning":
        warning_codes = [c for c in IssueCode if severity_of(c.value) is Severity.WARNING]
        stmt = stmt.where(
            or_(*(PurchaseDoc.issues.contains([{"code": c.value}]) for c in warning_codes))
        )
    if code is not None:
        stmt = stmt.where(PurchaseDoc.issues.contains([{"code": code}]))
    if date_from is not None:
        stmt = stmt.where(PurchaseDoc.issue_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(PurchaseDoc.issue_date <= date_to)
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


async def lines_by_doc(
    session: AsyncSession, purchase_doc_ids: list[UUID]
) -> dict[UUID, list[PurchaseDocLine]]:
    """Every line of the given purchase docs, grouped and in printed order (one query)."""
    grouped: dict[UUID, list[PurchaseDocLine]] = {doc_id: [] for doc_id in purchase_doc_ids}
    if not purchase_doc_ids:
        return grouped
    stmt = (
        select(PurchaseDocLine)
        .where(PurchaseDocLine.purchase_doc_id.in_(purchase_doc_ids))
        .order_by(PurchaseDocLine.purchase_doc_id, PurchaseDocLine.line_number)
    )
    for line in (await session.execute(stmt)).scalars():
        grouped[line.purchase_doc_id].append(line)
    return grouped


async def get_purchase_doc(
    session: AsyncSession, company_id: UUID, purchase_doc_id: UUID
) -> PurchaseDocView:
    doc = await session.get(PurchaseDoc, purchase_doc_id)
    if doc is None or doc.company_id != company_id:
        raise NotFoundError("purchase doc not found")
    supplier = await session.get(Supplier, doc.supplier_id) if doc.supplier_id else None
    lines = await _lines(session, doc.id)
    documents = list(
        (
            await session.execute(
                select(Document)
                .where(Document.purchase_doc_id == doc.id)
                .order_by(Document.created_at)
            )
        ).scalars()
    )
    corrections = list(
        (
            await session.execute(
                select(Correction)
                .where(Correction.purchase_doc_id == doc.id)
                .order_by(Correction.corrected_at)
            )
        ).scalars()
    )
    return PurchaseDocView(doc, supplier, lines, documents, corrections)


async def correct(
    session: AsyncSession,
    company: Company,
    purchase_doc_id: UUID,
    *,
    fields: dict[str, Any],
    lines: list[dict[str, Any]],
    reviewer: str,
) -> PurchaseDocView:
    """Apply the accountant's corrections, log each changed value, and recompute the issues."""
    view = await get_purchase_doc(session, company.id, purchase_doc_id)
    doc = view.doc
    unknown = set(fields) - HEADER_FIELDS
    if unknown:
        raise InvalidCorrectionError(f"unknown fields: {sorted(unknown)}")
    log: list[Correction] = []

    for name in HEADER_ORDER:
        if name not in fields:
            continue
        raw = fields[name]
        if name == "supplier_ruc":
            new_ruc = _ruc(raw)
            old_ruc = view.supplier.ruc if view.supplier else None
            if new_ruc != old_ruc:
                doc.supplier_id = await upsert_supplier(session, new_ruc, None) if new_ruc else None
                view.supplier = await session.get(Supplier, doc.supplier_id) if new_ruc else None
                log.append(_correction(doc.id, None, name, old_ruc, new_ruc, reviewer))
            continue
        if name == "supplier_name":
            new_name = _text(raw)
            if new_name != doc.supplier_name:
                log.append(_correction(doc.id, None, name, doc.supplier_name, new_name, reviewer))
                doc.supplier_name = new_name
            continue
        old = getattr(doc, name)
        new = _parse_header(name, raw)
        if new != old:
            setattr(doc, name, new)
            log.append(_correction(doc.id, None, name, _str(old), _str(new), reviewer))

    by_id = {line.id: line for line in view.lines}
    for patch in lines:
        unknown_line_fields = set(patch) - LINE_FIELDS - {"id"}
        if unknown_line_fields:
            raise InvalidCorrectionError(f"unknown line fields: {sorted(unknown_line_fields)}")
        line_id = _uuid(patch.get("id"))
        line = by_id.get(line_id) if line_id else None
        if line is None:
            raise InvalidCorrectionError(f"unknown line id: {patch.get('id')}")
        for name in LINE_ORDER:
            if name not in patch:
                continue
            raw = patch[name]
            old = getattr(line, name)
            new = _parse_line(name, raw)
            if new != old:
                setattr(line, name, new)
                log.append(_correction(doc.id, line.id, name, _str(old), _str(new), reviewer))

    if log:
        session.add_all(log)
        doc.issues = _recompute_issues(doc, view, company.ruc)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise CorrectionConflictError("purchase document business key already exists") from exc
    return await get_purchase_doc(session, company.id, purchase_doc_id)


async def delete_purchase_doc(
    session: AsyncSession, blobs: BlobStore, company_id: UUID, purchase_doc_id: UUID
) -> int:
    """Erase a purchase doc and every document that showed it (files, jobs, raw results), so
    the same file can be uploaded again from scratch. Returns how many documents went with it."""
    view = await get_purchase_doc(session, company_id, purchase_doc_id)
    document_ids = [document.id for document in view.documents]
    storage_keys = {document.storage_key for document in view.documents}
    await session.execute(delete(Correction).where(Correction.purchase_doc_id == purchase_doc_id))
    await session.execute(
        delete(PurchaseDocLine).where(PurchaseDocLine.purchase_doc_id == purchase_doc_id)
    )
    await session.execute(
        update(Document)
        .where(Document.purchase_doc_id == purchase_doc_id)
        .values(purchase_doc_id=None)
    )
    await session.execute(delete(PurchaseDoc).where(PurchaseDoc.id == purchase_doc_id))
    if document_ids:
        await session.execute(
            delete(ExtractionResult).where(ExtractionResult.document_id.in_(document_ids))
        )
        await session.execute(delete(Job).where(Job.document_id.in_(document_ids)))
        await session.execute(delete(Document).where(Document.id.in_(document_ids)))
    await session.commit()
    # Blobs are keyed by content hash and may be shared with another company's document.
    for key in storage_keys:
        still_used = await session.execute(
            select(Document.id).where(Document.storage_key == key).limit(1)
        )
        if still_used.first() is None:
            await blobs.delete(key)
    return len(document_ids)


def _recompute_issues(doc: PurchaseDoc, view: PurchaseDocView, company_ruc: str) -> list[Any]:
    """Checks run on the corrected values; duplicate notices are history and stay."""
    values = PurchaseDocValues(
        doc_type=doc.doc_type,
        supplier_ruc=view.supplier.ruc if view.supplier else None,
        supplier_name=doc.supplier_name,
        doc_number=doc.doc_number,
        issue_date=doc.issue_date,
        currency=doc.currency,
        total_amount=doc.total_amount,
        prices_include_igv=doc.prices_include_igv,
        buyer_ruc=doc.buyer_ruc,
        lines=tuple(
            LineValues(
                line.line_number,
                line.description,
                line.quantity,
                line.unit,
                line.unit_price,
                line.line_total,
            )
            for line in view.lines
        ),
    )
    kept = [i for i in doc.issues if i.get("code") == IssueCode.DUPLICATE_FILE.value]
    return kept + [issue.as_dict() for issue in find_issues(values, company_ruc)]


async def _lines(session: AsyncSession, purchase_doc_id: UUID) -> list[PurchaseDocLine]:
    stmt = (
        select(PurchaseDocLine)
        .where(PurchaseDocLine.purchase_doc_id == purchase_doc_id)
        .order_by(PurchaseDocLine.line_number)
    )
    return list((await session.execute(stmt)).scalars())


def _correction(
    purchase_doc_id: UUID,
    line_id: UUID | None,
    field: str,
    old: str | None,
    new: str | None,
    reviewer: str,
) -> Correction:
    return Correction(
        purchase_doc_id=purchase_doc_id,
        line_id=line_id,
        field=field,
        old_value=old,
        new_value=new,
        corrected_by=reviewer,
    )


def _parse_header(name: str, raw: Any) -> Any:
    if raw is None:
        return None
    try:
        match name:
            case "doc_type":
                return DocType(str(raw))
            case "issue_date":
                return date.fromisoformat(str(raw))
            case "total_amount":
                return Decimal(str(raw))
            case "prices_include_igv":
                if not isinstance(raw, bool):
                    raise InvalidCorrectionError("prices_include_igv must be true, false or null")
                return raw
            case "currency":
                return str(raw).strip().upper()[:3]
            case "buyer_ruc":
                return _ruc(raw)
            case "doc_number":
                return normalize_doc_number(str(raw))
            case _:
                return _text(raw)
    except (ValueError, InvalidOperation) as exc:
        raise InvalidCorrectionError(f"invalid value for {name}") from exc


def _parse_line(name: str, raw: Any) -> Any:
    if name == "unit":
        return normalize_unit(str(raw or ""))
    if raw is None:
        return None
    if name == "description":
        return _text(raw)
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise InvalidCorrectionError(f"invalid value for {name}") from exc


def _ruc(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    if len(text) != 11 or not text.isdigit():
        raise InvalidCorrectionError("a RUC is 11 digits")
    return text


def _text(raw: Any) -> str | None:
    text = " ".join(str(raw).split()) if raw is not None else ""
    return text or None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value.value if hasattr(value, "value") else value)


def _uuid(raw: Any) -> UUID | None:
    try:
        return UUID(str(raw))
    except ValueError:
        return None
