"""Store supplier name as a purchase-record snapshot.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("purchase_docs", sa.Column("supplier_name", sa.Text(), nullable=True))
    op.execute(
        "UPDATE purchase_docs AS p SET supplier_name = s.legal_name "
        "FROM suppliers AS s WHERE p.supplier_id = s.id"
    )


def downgrade() -> None:
    op.drop_column("purchase_docs", "supplier_name")
