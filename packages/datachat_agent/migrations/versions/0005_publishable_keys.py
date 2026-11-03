"""publishable (browser) API keys tied to websites

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("key_type", sa.String(length=20), server_default="secret", nullable=False),
    )
    op.add_column(
        "api_keys",
        sa.Column(
            "allowed_domains",
            postgresql.ARRAY(sa.String(length=253)),
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "allowed_domains")
    op.drop_column("api_keys", "key_type")
