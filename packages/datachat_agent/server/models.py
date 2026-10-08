"""DataChat's own tables. The customer's database is only ever read, never written."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from datachat_agent.config import get_settings

EMBED_DIM = get_settings().embed_dim


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _tenant_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(50), default="free")
    # Hosted mode: each tenant brings its own LLM key, encrypted with Fernet.
    llm_provider: Mapped[str | None] = mapped_column(String(50))
    llm_key_encrypted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(100))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(20))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(20)))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created_at()


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(100))
    dialect: Mapped[str] = mapped_column(String(20))  # postgresql | mysql
    dsn_encrypted: Mapped[str] = mapped_column(Text)
    schema_allowlist: Mapped[list[str]] = mapped_column(ARRAY(String(200)), default=list)
    # Row-level filter templates, e.g. {"orders": "company_id = :user_company"}
    row_filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = _created_at()


class SchemaItem(Base):
    __tablename__ = "schema_items"

    id: Mapped[uuid.UUID] = _pk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    table_name: Mapped[str] = mapped_column(String(200))
    column_name: Mapped[str | None] = mapped_column(String(200))  # NULL = the table itself
    data_type: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    sample_values: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))

    __table_args__ = (
        UniqueConstraint(
            "connection_id", "table_name", "column_name", postgresql_nulls_not_distinct=True
        ),
        Index(
            "ix_schema_items_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    filename: Mapped[str] = mapped_column(String(500))
    mime: Mapped[str] = mapped_column(String(100), default="application/pdf")
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    file_bytes: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    pages: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|ready|failed
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (UniqueConstraint("tenant_id", "sha256"),)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))

    __table_args__ = (
        Index("ix_document_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_document_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class DocumentItem(Base):
    __tablename__ = "document_items"

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page: Mapped[int] = mapped_column(Integer)
    row_index: Mapped[int] = mapped_column(Integer)
    item: Mapped[str | None] = mapped_column(Text)
    qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unit: Mapped[str | None] = mapped_column(String(50))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ChatSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    user_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created_at()


class QueryLog(Base):
    __tablename__ = "query_log"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(String(20))  # database|documents|both
    sql: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int | None] = mapped_column(Integer)
    chunks_used: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    answer: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    feedback: Mapped[str | None] = mapped_column(String(20))  # helpful|wrong
    created_at: Mapped[datetime] = _created_at()
