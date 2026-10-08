"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-08 14:35:52.931992
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from datachat_agent.config import get_settings

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Vector size is fixed when the tables are created; it must match EMBED_DIM.
EMBED_DIM = get_settings().embed_dim


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "tenants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("plan", sa.String(length=50), nullable=False),
        sa.Column("llm_provider", sa.String(length=50), nullable=True),
        sa.Column("llm_key_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=20), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String(length=20)), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_index(op.f("ix_api_keys_tenant_id"), "api_keys", ["tenant_id"], unique=False)
    op.create_table(
        "connections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("dialect", sa.String(length=20), nullable=False),
        sa.Column("dsn_encrypted", sa.Text(), nullable=False),
        sa.Column("schema_allowlist", postgresql.ARRAY(sa.String(length=200)), nullable=False),
        sa.Column("row_filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_connections_tenant_id"), "connections", ["tenant_id"], unique=False)
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("mime", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("file_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "sha256"),
    )
    op.create_index(op.f("ix_documents_tenant_id"), "documents", ["tenant_id"], unique=False)
    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_ref", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sessions_tenant_id"), "sessions", ["tenant_id"], unique=False)
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=False,
        ),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_document_chunks_document_id"), "document_chunks", ["document_id"], unique=False
    )
    op.create_index(
        "ix_document_chunks_embedding",
        "document_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_document_chunks_tsv", "document_chunks", ["tsv"], unique=False, postgresql_using="gin"
    )
    op.create_table(
        "document_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("item", sa.Text(), nullable=True),
        sa.Column("qty", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("rate", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_document_items_document_id"), "document_items", ["document_id"], unique=False
    )
    op.create_table(
        "query_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("route", sa.String(length=20), nullable=True),
        sa.Column("sql", sa.Text(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("chunks_used", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("feedback", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_query_log_session_id"), "query_log", ["session_id"], unique=False)
    op.create_index(op.f("ix_query_log_tenant_id"), "query_log", ["tenant_id"], unique=False)
    op.create_table(
        "schema_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("schema_name", sa.String(length=200), nullable=False),
        sa.Column("table_name", sa.String(length=200), nullable=False),
        sa.Column("column_name", sa.String(length=200), nullable=True),
        sa.Column("data_type", sa.String(length=100), nullable=True),
        sa.Column("is_primary_key", sa.Boolean(), nullable=False),
        sa.Column("foreign_key", sa.String(length=600), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sample_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=True),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id",
            "schema_name",
            "table_name",
            "column_name",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_schema_items_connection_id"), "schema_items", ["connection_id"], unique=False
    )
    op.create_index(
        "ix_schema_items_embedding",
        "schema_items",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schema_items_embedding",
        table_name="schema_items",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index(op.f("ix_schema_items_connection_id"), table_name="schema_items")
    op.drop_table("schema_items")
    op.drop_index(op.f("ix_query_log_tenant_id"), table_name="query_log")
    op.drop_index(op.f("ix_query_log_session_id"), table_name="query_log")
    op.drop_table("query_log")
    op.drop_index(op.f("ix_document_items_document_id"), table_name="document_items")
    op.drop_table("document_items")
    op.drop_index("ix_document_chunks_tsv", table_name="document_chunks", postgresql_using="gin")
    op.drop_index(
        "ix_document_chunks_embedding",
        table_name="document_chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index(op.f("ix_document_chunks_document_id"), table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index(op.f("ix_sessions_tenant_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_documents_tenant_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_connections_tenant_id"), table_name="connections")
    op.drop_table("connections")
    op.drop_index(op.f("ix_api_keys_tenant_id"), table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("tenants")
