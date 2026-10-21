"""documents: printed date; chunks: section heading

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("doc_date", sa.Date(), nullable=True))
    op.add_column("document_chunks", sa.Column("heading", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "heading")
    op.drop_column("documents", "doc_date")
