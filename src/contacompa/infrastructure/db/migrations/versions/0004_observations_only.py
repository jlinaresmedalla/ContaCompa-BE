"""observations only: no review status; one printed document number

Purchase docs are stored as processed and carry observations; there is no approval step
(ADR 0007). The series and number columns merge into doc_number, stored as printed.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

review_status = postgresql.ENUM(
    "pending_review", "approved", "rejected", name="review_status", create_type=False
)


def upgrade() -> None:
    op.add_column("purchase_docs", sa.Column("doc_number", sa.String(40), nullable=True))
    op.execute(
        "UPDATE purchase_docs SET doc_number = series || '-' || number "
        "WHERE series IS NOT NULL AND number IS NOT NULL"
    )
    op.drop_constraint("uq_purchase_docs_business_key", "purchase_docs", type_="unique")
    op.drop_index("ix_purchase_docs_review", table_name="purchase_docs")
    for column in ("series", "number", "status", "review_note", "approved_at", "approved_by"):
        op.drop_column("purchase_docs", column)
    review_status.drop(op.get_bind(), checkfirst=True)
    op.create_unique_constraint(
        "uq_purchase_docs_business_key",
        "purchase_docs",
        ["company_id", "supplier_id", "doc_type", "doc_number"],
    )
    op.create_index("ix_purchase_docs_company_date", "purchase_docs", ["company_id", "issue_date"])


def downgrade() -> None:
    review_status.create(op.get_bind(), checkfirst=True)
    op.drop_index("ix_purchase_docs_company_date", table_name="purchase_docs")
    op.drop_constraint("uq_purchase_docs_business_key", "purchase_docs", type_="unique")
    op.add_column("purchase_docs", sa.Column("series", sa.String(8), nullable=True))
    op.add_column("purchase_docs", sa.Column("number", sa.String(20), nullable=True))
    op.add_column(
        "purchase_docs",
        sa.Column(
            "status", review_status, nullable=False, server_default=sa.text("'pending_review'")
        ),
    )
    op.add_column("purchase_docs", sa.Column("review_note", sa.Text(), nullable=True))
    op.add_column(
        "purchase_docs", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("purchase_docs", sa.Column("approved_by", sa.String(128), nullable=True))
    op.execute(
        "UPDATE purchase_docs SET series = left(split_part(doc_number, '-', 1), 8), "
        "number = left(split_part(doc_number, '-', 2), 20)"
    )
    op.drop_column("purchase_docs", "doc_number")
    op.create_unique_constraint(
        "uq_purchase_docs_business_key",
        "purchase_docs",
        ["company_id", "supplier_id", "doc_type", "series", "number"],
    )
    op.create_index(
        "ix_purchase_docs_review", "purchase_docs", ["company_id", "status", "issue_date"]
    )
