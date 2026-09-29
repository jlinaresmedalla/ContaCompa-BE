"""review by exception: who accepted a purchase doc ('auto' or a reviewer)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("purchase_docs", sa.Column("approved_by", sa.String(128), nullable=True))
    op.execute("UPDATE purchase_docs SET approved_by = 'accountant' WHERE status = 'approved'")


def downgrade() -> None:
    op.drop_column("purchase_docs", "approved_by")
