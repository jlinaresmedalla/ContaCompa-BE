"""api_keys: short-lived company keys replace the single key hash on companies (ADR 0017).

Existing sign-in keys stop working; mint new ones with POST /v1/api-keys.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_keys_company_id", "api_keys", ["company_id"])
    op.drop_column("companies", "api_key_hash")


def downgrade() -> None:
    # Restored with unmatchable hashes, as migration 0002 seeds them.
    op.add_column("companies", sa.Column("api_key_hash", sa.String(64), nullable=True))
    op.execute(
        "UPDATE companies SET api_key_hash = "
        "encode(sha256(convert_to(gen_random_uuid()::text, 'UTF8')), 'hex')"
    )
    op.alter_column("companies", "api_key_hash", nullable=False)
    op.create_unique_constraint("companies_api_key_hash_key", "companies", ["api_key_hash"])
    op.drop_index("ix_api_keys_company_id", table_name="api_keys")
    op.drop_table("api_keys")
