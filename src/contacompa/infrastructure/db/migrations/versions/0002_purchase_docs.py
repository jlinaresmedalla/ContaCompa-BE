"""reviewed layer: companies, suppliers, purchase_docs, purchase_doc_lines, corrections

Documents gain a company, a MIME type, a source kind and a link to their purchase doc, and lose
the caller-declared type (ADR 0005).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24

"""

import hashlib
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from contacompa.config import get_settings

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
source_kind = postgresql.ENUM(
    "pdf_text", "pdf_scanned", "photo", name="source_kind", create_type=False
)
doc_type = postgresql.ENUM(
    "invoice",
    "sales_receipt",
    "sales_note",
    "credit_note",
    "other",
    name="doc_type",
    create_type=False,
)
review_status = postgresql.ENUM(
    "pending_review", "approved", "rejected", name="review_status", create_type=False
)
document_type = postgresql.ENUM(
    "invoice", "purchase_order", "receipt", name="document_type", create_type=False
)


def _now() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (source_kind, doc_type, review_status):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "companies",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("ruc", sa.CHAR(11), nullable=False, unique=True),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("api_key_hash", sa.String(64), nullable=False, unique=True),
        _now(),
    )
    op.create_table(
        "suppliers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("ruc", sa.CHAR(11), nullable=False, unique=True),
        sa.Column("legal_name", sa.Text(), nullable=True),
        _now(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "purchase_docs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("supplier_id", UUID, sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("doc_type", doc_type, nullable=False),
        sa.Column("series", sa.String(8), nullable=True),
        sa.Column("number", sa.String(20), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=True),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("prices_include_igv", sa.Boolean(), nullable=True),
        sa.Column("buyer_ruc", sa.CHAR(11), nullable=True),
        sa.Column(
            "status", review_status, nullable=False, server_default=sa.text("'pending_review'")
        ),
        sa.Column(
            "issues", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("source_result_id", UUID, sa.ForeignKey("extraction_results.id"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        _now(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "company_id",
            "supplier_id",
            "doc_type",
            "series",
            "number",
            name="uq_purchase_docs_business_key",
        ),
    )
    op.create_index(
        "ix_purchase_docs_review", "purchase_docs", ["company_id", "status", "issue_date"]
    )
    op.create_table(
        "purchase_doc_lines",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "purchase_doc_id",
            UUID,
            sa.ForeignKey("purchase_docs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("unit", sa.String(32), nullable=False, server_default="unit"),
        sa.Column("unit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=True),
        sa.UniqueConstraint("purchase_doc_id", "line_number", name="uq_purchase_doc_lines_number"),
    )
    op.create_table(
        "corrections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("purchase_doc_id", UUID, sa.ForeignKey("purchase_docs.id"), nullable=False),
        sa.Column("line_id", UUID, sa.ForeignKey("purchase_doc_lines.id"), nullable=True),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("corrected_by", sa.String(128), nullable=False),
        sa.Column(
            "corrected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_corrections_purchase_doc_id", "corrections", ["purchase_doc_id"])

    # Seed the company the configured API key belongs to, and give it every existing document.
    settings = get_settings()
    company_id = uuid.uuid4()
    key_hash = hashlib.sha256(settings.api_key.get_secret_value().encode()).hexdigest()
    op.execute(
        sa.text(
            "INSERT INTO companies (id, ruc, legal_name, api_key_hash) VALUES (:id, :ruc, :n, :k)"
        ).bindparams(id=company_id, ruc=settings.company_ruc, n=settings.company_name, k=key_hash)
    )

    op.add_column("documents", sa.Column("company_id", UUID, nullable=True))
    op.add_column("documents", sa.Column("mime_type", sa.String(64), nullable=True))
    op.add_column("documents", sa.Column("source_kind", source_kind, nullable=True))
    op.add_column("documents", sa.Column("width_px", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("height_px", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("purchase_doc_id", UUID, nullable=True))
    op.execute(
        sa.text(
            "UPDATE documents SET company_id = :id, mime_type = 'application/pdf', "
            "source_kind = 'pdf_text'"
        ).bindparams(id=company_id)
    )
    op.alter_column("documents", "company_id", nullable=False)
    op.alter_column("documents", "mime_type", nullable=False)
    op.alter_column("documents", "source_kind", nullable=False)
    op.create_foreign_key(
        "fk_documents_company_id", "documents", "companies", ["company_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_documents_purchase_doc_id", "documents", "purchase_docs", ["purchase_doc_id"], ["id"]
    )
    op.create_index("ix_documents_company_id", "documents", ["company_id"])
    op.create_index("ix_documents_purchase_doc_id", "documents", ["purchase_doc_id"])
    op.drop_constraint("documents_sha256_key", "documents", type_="unique")
    op.create_unique_constraint(
        "uq_documents_company_sha256", "documents", ["company_id", "sha256"]
    )
    op.drop_column("documents", "declared_type")
    op.drop_column("extraction_results", "type_mismatch")
    document_type.drop(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    document_type.create(bind, checkfirst=True)
    op.add_column(
        "extraction_results",
        sa.Column("type_mismatch", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "documents",
        sa.Column(
            "declared_type", document_type, nullable=False, server_default=sa.text("'invoice'")
        ),
    )
    op.alter_column("documents", "declared_type", server_default=None)
    op.drop_constraint("uq_documents_company_sha256", "documents", type_="unique")
    op.create_unique_constraint("documents_sha256_key", "documents", ["sha256"])
    op.drop_index("ix_documents_purchase_doc_id", table_name="documents")
    op.drop_index("ix_documents_company_id", table_name="documents")
    op.drop_constraint("fk_documents_purchase_doc_id", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_company_id", "documents", type_="foreignkey")
    for column in ("purchase_doc_id", "height_px", "width_px", "source_kind", "mime_type"):
        op.drop_column("documents", column)
    op.drop_column("documents", "company_id")

    op.drop_index("ix_corrections_purchase_doc_id", table_name="corrections")
    op.drop_table("corrections")
    op.drop_table("purchase_doc_lines")
    op.drop_index("ix_purchase_docs_review", table_name="purchase_docs")
    op.drop_table("purchase_docs")
    op.drop_table("suppliers")
    op.drop_table("companies")
    for enum_type in (review_status, doc_type, source_kind):
        enum_type.drop(bind, checkfirst=True)
