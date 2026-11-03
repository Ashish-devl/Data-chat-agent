"""Use DataChat Agent inside a Python program, without running the API server.

    from datachat_agent import DataChat

    dc = DataChat("postgresql://readonly:secret@host/erp")
    dc.scan_schema()
    dc.add_pdf("invoice_8231.pdf")
    print(dc.ask("What is the total of invoice KE/26-27/8231?").answer)

DataChat keeps what it learns (schema, passages, conversations) in its own Postgres with pgvector:
DATABASE_URL from the environment or .env, or `storage_url=`. The model comes from the LLM_*
settings, or pass `llm=` (a LangChain chat model, or a model name for the configured provider).
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


@dataclass
class Answer:
    question: str
    answer: str
    route: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    chart: dict[str, str] | None = None
    session_id: str | None = None
    tokens: int = 0
    latency_ms: int = 0
    error: str | None = None

    def __str__(self) -> str:
        return self.answer


@dataclass
class DocumentInfo:
    id: uuid.UUID
    filename: str
    status: str
    pages: int | None
    error: str | None
    created: bool


def _library_secret() -> str:
    """A key that encrypts the stored connection string, kept in the user's config folder."""
    path = Path.home() / ".config" / "datachat-agent" / "secret.key"
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key().decode()
    path.write_text(key)
    try:
        path.chmod(0o600)
    except OSError:  # Windows ignores POSIX modes
        pass
    return key


class DataChat:
    """One connected database plus uploaded PDFs. Methods block; `a`-prefixed ones are async."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        storage_url: str | None = None,
        llm: Any = None,
        embedder: Any = None,
        name: str = "default",
        secret_key: str | None = None,
        migrate: bool = True,
    ) -> None:
        from datachat_agent.config import get_settings

        if storage_url:
            os.environ["DATABASE_URL"] = storage_url
        if secret_key:
            os.environ["SECRET_KEY"] = secret_key
        get_settings.cache_clear()
        if not get_settings().secret_key:
            os.environ["SECRET_KEY"] = _library_secret()
            get_settings.cache_clear()

        if migrate:
            from concurrent.futures import ThreadPoolExecutor

            from datachat_agent.server.db import run_migrations

            # Alembic runs its own event loop; a separate thread keeps that working even when
            # DataChat is created inside async code.
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(run_migrations).result()

        self._llm_arg = llm
        self._embedder_arg = embedder
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._closed = False
        self._call(self._setup(name, database_url))

    # --- plumbing -----------------------------------------------------------------------

    def _check_open(self, coro) -> None:
        if self._closed:
            coro.close()
            raise RuntimeError("This DataChat has been closed.")

    def _call(self, coro):
        self._check_open(coro)
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _acall(self, coro):
        self._check_open(coro)
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._loop))

    @property
    def llm(self):
        if self._llm_arg is None or isinstance(self._llm_arg, str):
            from datachat_agent.config import get_settings
            from datachat_agent.core.llm import get_chat_model

            settings = get_settings()
            if isinstance(self._llm_arg, str):
                settings = settings.model_copy(update={"llm_model": self._llm_arg})
            return get_chat_model(settings)
        return self._llm_arg

    @property
    def embedder(self):
        if self._embedder_arg is None:
            from datachat_agent.core.embeddings import get_embedder

            return get_embedder()
        return self._embedder_arg

    async def _setup(self, name: str, database_url: str | None) -> None:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from datachat_agent.config import get_settings
        from datachat_agent.security import decrypt, encrypt
        from datachat_agent.server.models import Connection, Tenant
        from datachat_agent.sql.connector import normalize_dsn

        # Its own engine on its own event loop, independent of any API server in this process.
        self._engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)
        async with self._sessions() as s:
            tenant_name = f"library:{name}"
            tenant = await s.scalar(select(Tenant).where(Tenant.name == tenant_name))
            if tenant is None:
                tenant = Tenant(name=tenant_name)
                s.add(tenant)
                await s.commit()
            self.tenant_id = tenant.id
            self.connection_id = None
            if database_url:
                dialect, _ = normalize_dsn(database_url)
                conn = await s.scalar(
                    select(Connection).where(
                        Connection.tenant_id == tenant.id, Connection.name == "library"
                    )
                )
                if conn is None:
                    conn = Connection(
                        tenant_id=tenant.id,
                        name="library",
                        dialect=dialect,
                        dsn_encrypted=encrypt(database_url),
                        schema_allowlist=[],
                        row_filters={},
                        status="new",
                    )
                    s.add(conn)
                elif decrypt(conn.dsn_encrypted) != database_url:
                    conn.dsn_encrypted, conn.dialect, conn.status = (
                        encrypt(database_url),
                        dialect,
                        "new",
                    )
                await s.commit()
                self.connection_id = conn.id

    async def _connection(self, s):
        from datachat_agent.server.models import Connection

        if self.connection_id is None:
            return None
        return await s.get(Connection, self.connection_id)

    # --- database -----------------------------------------------------------------------

    async def ascan_schema(self, schemas: list[str] | None = None) -> dict[str, int]:
        return await self._acall(self._scan(schemas))

    def scan_schema(self, schemas: list[str] | None = None) -> dict[str, int]:
        """Check the user is read-only, then read tables, columns, keys and sample values."""
        return self._call(self._scan(schemas))

    async def _scan(self, schemas):
        from datachat_agent.server.services import scan_connection

        async with self._sessions() as s:
            conn = await self._connection(s)
            if conn is None:
                raise RuntimeError("No database_url was given to DataChat().")
            if schemas is not None:
                conn.schema_allowlist = schemas
                await s.commit()
            return await scan_connection(s, conn)

    def hide(self, table: str, column: str | None = None) -> None:
        """Hide a table or a column: the model never sees it and queries cannot touch it."""
        self._call(self._hide(table, column))

    async def _hide(self, table: str, column: str | None) -> None:
        from sqlalchemy import select

        from datachat_agent.server.models import SchemaItem

        schema, _, name = table.rpartition(".")
        async with self._sessions() as s:
            q = select(SchemaItem).where(
                SchemaItem.connection_id == self.connection_id,
                SchemaItem.table_name == name,
                SchemaItem.column_name.is_(None)
                if column is None
                else SchemaItem.column_name == column,
            )
            if schema:
                q = q.where(SchemaItem.schema_name == schema)
            items = list(await s.scalars(q))
            if not items:
                raise KeyError(f"Not found in the scanned schema: {table} {column or ''}".strip())
            for item in items:
                item.hidden, item.sample_values = True, []
            await s.commit()

    def add_term(self, term: str, definition: str) -> None:
        """Teach a business term, e.g. add_term("open PO", "status is Open or Partly Received")."""
        self._call(self._add_term(term, definition))

    async def _add_term(self, term: str, definition: str) -> None:
        from datachat_agent.agent.knowledge import term_text
        from datachat_agent.server.models import GlossaryTerm

        vector = await asyncio.to_thread(
            self.embedder.embed_documents, [term_text(term, definition)]
        )
        async with self._sessions() as s:
            s.add(
                GlossaryTerm(
                    tenant_id=self.tenant_id,
                    connection_id=self.connection_id,
                    term=term,
                    definition=definition,
                    embedding=vector[0],
                )
            )
            await s.commit()

    def add_example(self, question: str, sql: str) -> None:
        """A worked question -> SQL pair. The SQL must pass the same safety checks as any query."""
        self._call(self._add_example(question, sql))

    async def _add_example(self, question: str, sql: str) -> None:
        from datachat_agent.config import get_settings
        from datachat_agent.server.models import SqlExample
        from datachat_agent.sql.schema_store import build_policy
        from datachat_agent.sql.validator import validate_sql

        async with self._sessions() as s:
            conn = await self._connection(s)
            policy = await build_policy(s, conn)
            policy.row_filters = {}
            validated = validate_sql(sql, conn.dialect, policy, get_settings().sql_max_rows)
            vector = await asyncio.to_thread(self.embedder.embed_documents, [question])
            s.add(
                SqlExample(
                    tenant_id=self.tenant_id,
                    connection_id=conn.id,
                    question=question,
                    sql=validated.sql,
                    embedding=vector[0],
                )
            )
            await s.commit()

    # --- documents ----------------------------------------------------------------------

    async def aadd_pdf(self, pdf: str | Path | bytes, filename: str | None = None) -> DocumentInfo:
        return await self._acall(self._add_pdf(pdf, filename))

    def add_pdf(self, pdf: str | Path | bytes, filename: str | None = None) -> DocumentInfo:
        """Store a PDF and make it searchable. The same file twice is stored once."""
        return self._call(self._add_pdf(pdf, filename))

    async def _add_pdf(self, pdf, filename):
        from datachat_agent.config import get_settings
        from datachat_agent.docs.ingest import ingest_document, store_document

        if isinstance(pdf, bytes):
            data, name = pdf, filename or "document.pdf"
        else:
            path = Path(pdf)
            data, name = path.read_bytes(), filename or path.name
        async with self._sessions() as s:
            stored = await store_document(
                s, self.tenant_id, name, data, get_settings().max_pdf_mb * 1_000_000
            )
            doc = stored.document
            if stored.created:
                doc = await ingest_document(s, doc.id, self.embedder, self.llm)
            return DocumentInfo(
                doc.id, doc.filename, doc.status, doc.pages, doc.error, stored.created
            )

    def documents(self) -> list[DocumentInfo]:
        return self._call(self._documents())

    async def _documents(self):
        from sqlalchemy import select

        from datachat_agent.server.models import Document

        async with self._sessions() as s:
            docs = await s.scalars(
                select(Document)
                .where(Document.tenant_id == self.tenant_id)
                .order_by(Document.created_at)
            )
            return [DocumentInfo(d.id, d.filename, d.status, d.pages, d.error, False) for d in docs]

    # --- questions ----------------------------------------------------------------------

    async def aask(
        self,
        question: str,
        session_id: str | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> Answer:
        return await self._acall(self._ask(question, session_id, user_context))

    def ask(
        self,
        question: str,
        session_id: str | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> Answer:
        """Answer from the database and/or the PDFs. Reuse session_id for follow-up questions."""
        return self._call(self._ask(question, session_id, user_context))

    async def _ask(self, question, session_id, user_context) -> Answer:
        from datachat_agent.agent.service import answer_and_log

        started = time.perf_counter()
        async with self._sessions() as s:
            result, chat, _ = await answer_and_log(
                s,
                self.tenant_id,
                await self._connection(s),
                question,
                self.llm,
                self.embedder,
                session_id,
                user_context,
            )
        data = result.sql.result if result.sql else None
        return Answer(
            question=question,
            answer=result.answer,
            route=result.route,
            sql=result.sql.sql if result.sql and data else None,
            columns=data.columns if data else [],
            rows=data.rows if data else [],
            citations=result.citations,
            chart=result.chart,
            session_id=chat.client_key,
            tokens=result.tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=result.error,
        )

    # --- lifecycle ----------------------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        asyncio.run_coroutine_threadsafe(self._engine.dispose(), self._loop).result()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._closed = True

    def __enter__(self) -> DataChat:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
