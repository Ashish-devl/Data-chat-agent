"""tenant limits; feedback comment and time

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True))
    op.add_column("tenants", sa.Column("monthly_token_limit", sa.BigInteger(), nullable=True))
    op.add_column("query_log", sa.Column("feedback_comment", sa.Text(), nullable=True))
    op.add_column("query_log", sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("query_log", "feedback_at")
    op.drop_column("query_log", "feedback_comment")
    op.drop_column("tenants", "monthly_token_limit")
    op.drop_column("tenants", "rate_limit_per_minute")
