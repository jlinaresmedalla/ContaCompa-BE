"""Materialize a raw extraction into purchase doc records: supplier, purchase doc, lines and the
observations the checks found. Nothing waits for approval (ADR 0007).

Runs inside the caller's transaction and never commits."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from contacompa.domain.checks import Issue, IssueCode, find_issues
from contacompa.domain.purchase_doc import PurchaseDocValues, values_from_fields
from contacompa.infrastructure.db.models import (
    Company,
    Document,
    PurchaseDoc,
    PurchaseDocLine,
    Supplier,
)


async def upsert_supplier(session: AsyncSession, ruc: str, legal_name: str | None) -> UUID:
    """Suppliers are shared across companies; the first name seen is kept until corrected.
    DO UPDATE (not DO NOTHING) so the existing row's id comes back from RETURNING."""
    insert_stmt = insert(Supplier).values(ruc=ruc, legal_name=legal_name)
    upsert = insert_stmt.on_conflict_do_update(
        index_elements=[Supplier.ruc],
        set_={"legal_name": func.coalesce(Supplier.legal_name, insert_stmt.excluded.legal_name)},
    ).returning(Supplier.id)
    supplier_id: UUID = (await session.execute(upsert)).scalar_one()
    return supplier_id


async def materialize(
    session: AsyncSession,
    *,
    company: Company,
    document: Document,
    result_id: UUID,
    doc_type: str,
    fields: dict[str, Any],
) -> PurchaseDoc:
    values = values_from_fields(doc_type, fields)
    supplier_id = (
        await upsert_supplier(session, values.supplier_ruc, values.supplier_name)
        if values.supplier_ruc
        else None
    )
    existing = await _find_by_business_key(session, company.id, supplier_id, values)
    if existing is not None:
        # Another document already showed this purchase doc: link it, flag it, change nothing.
        # Two workers racing on the same key hit the unique constraint; the retry lands here.
        if document.purchase_doc_id != existing.id:
            duplicate = Issue(
                IssueCode.DUPLICATE_FILE, f"also shown by {document.filename}", field=None
            )
            existing.issues = [*existing.issues, duplicate.as_dict()]
            document.purchase_doc_id = existing.id
        return existing

    issues = [issue.as_dict() for issue in find_issues(values, company.ruc)]
    purchase_doc = PurchaseDoc(
        company_id=company.id,
        supplier_id=supplier_id,
        supplier_name=values.supplier_name,
        doc_type=values.doc_type,
        doc_number=values.doc_number,
        issue_date=values.issue_date,
        currency=values.currency,
        total_amount=values.total_amount,
        prices_include_igv=values.prices_include_igv,
        buyer_ruc=values.buyer_ruc,
        issues=issues,
        source_result_id=result_id,
    )
    session.add(purchase_doc)
    await session.flush()
    session.add_all(
        PurchaseDocLine(
            purchase_doc_id=purchase_doc.id,
            line_number=line.line_number,
            description=line.description,
            quantity=line.quantity,
            unit=line.unit,
            unit_price=line.unit_price,
            line_total=line.line_total,
        )
        for line in values.lines
    )
    document.purchase_doc_id = purchase_doc.id
    return purchase_doc


async def _find_by_business_key(
    session: AsyncSession, company_id: UUID, supplier_id: UUID | None, values: PurchaseDocValues
) -> PurchaseDoc | None:
    if supplier_id is None or values.business_key is None:
        return None
    stmt = select(PurchaseDoc).where(
        PurchaseDoc.company_id == company_id,
        PurchaseDoc.supplier_id == supplier_id,
        PurchaseDoc.doc_type == values.doc_type,
        PurchaseDoc.doc_number == values.doc_number,
    )
    return (await session.execute(stmt)).scalar_one_or_none()
