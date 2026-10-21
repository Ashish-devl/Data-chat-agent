"""agent: session keys, glossary terms, SQL examples, richer query log

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21 14:47:49.038393
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

from datachat_agent.config import get_settings

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBED_DIM = get_settings().embed_dim


def upgrade() -> None:
    op.create_table(
        "glossary_terms",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("term", sa.String(length=200), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "term"),
    )
    op.create_index(op.f("ix_glossary_terms_connection_id"), "glossary_terms", ["connection_id"])
    op.create_index(op.f("ix_glossary_terms_tenant_id"), "glossary_terms", ["tenant_id"])

    op.create_table(
        "sql_examples",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sql_examples_connection_id"), "sql_examples", ["connection_id"])
    op.create_index(op.f("ix_sql_examples_tenant_id"), "sql_examples", ["tenant_id"])

    op.add_column("query_log", sa.Column("connection_id", sa.UUID(), nullable=True))
    op.add_column("query_log", sa.Column("api_key_id", sa.UUID(), nullable=True))
    op.add_column("query_log", sa.Column("error", sa.Text(), nullable=True))
    op.create_index("ix_query_log_tenant_created", "query_log", ["tenant_id", "created_at"])
    op.create_foreign_key(
        "query_log_api_key_id_fkey",
        "query_log",
        "api_keys",
        ["api_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "query_log_connection_id_fkey",
        "query_log",
        "connections",
        ["connection_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Existing sessions get their own id as key, then the column becomes required.
    op.add_column("sessions", sa.Column("client_key", sa.String(length=100), nullable=True))
    op.execute("UPDATE sessions SET client_key = id::text WHERE client_key IS NULL")
    op.alter_column("sessions", "client_key", nullable=False)
    op.create_unique_constraint(
        "sessions_tenant_id_client_key_key", "sessions", ["tenant_id", "client_key"]
    )


def downgrade() -> None:
    op.drop_constraint("sessions_tenant_id_client_key_key", "sessions", type_="unique")
    op.drop_column("sessions", "client_key")
    op.drop_constraint("query_log_connection_id_fkey", "query_log", type_="foreignkey")
    op.drop_constraint("query_log_api_key_id_fkey", "query_log", type_="foreignkey")
    op.drop_index("ix_query_log_tenant_created", table_name="query_log")
    op.drop_column("query_log", "error")
    op.drop_column("query_log", "api_key_id")
    op.drop_column("query_log", "connection_id")
    op.drop_index(op.f("ix_sql_examples_tenant_id"), table_name="sql_examples")
    op.drop_index(op.f("ix_sql_examples_connection_id"), table_name="sql_examples")
    op.drop_table("sql_examples")
    op.drop_index(op.f("ix_glossary_terms_tenant_id"), table_name="glossary_terms")
    op.drop_index(op.f("ix_glossary_terms_connection_id"), table_name="glossary_terms")
    op.drop_table("glossary_terms")
