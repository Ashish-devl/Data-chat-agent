"""Work shared by the API routes and the background worker."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from datachat_agent.core.embeddings import get_embedder
from datachat_agent.security import decrypt
from datachat_agent.server.models import Connection, SchemaItem
from datachat_agent.sql.connector import check_read_only, get_engine
from datachat_agent.sql.schema_scanner import scan_schema
from datachat_agent.sql.schema_store import save_scan


class ScanRefused(Exception):
    """The connection failed the read-only check."""


class ScanFailed(Exception):
    """The database could not be read."""


async def test_connection(session: AsyncSession, conn: Connection) -> bool:
    engine = get_engine(decrypt(conn.dsn_encrypted))
    result = await run_in_threadpool(check_read_only, engine, conn.dialect)
    conn.status = "ok" if result.ok else "rejected"
    conn.status_detail = result.detail
    await session.commit()
    await session.refresh(conn)
    return result.ok


async def scan_connection(session: AsyncSession, conn: Connection) -> dict[str, int]:
    # Re-check every time: grants can change after the first test.
    if not await test_connection(session, conn):
        raise ScanRefused(conn.status_detail or "Connection rejected")
    hidden = {
        (i.schema_name, i.table_name, i.column_name)
        for i in await session.scalars(
            select(SchemaItem).where(SchemaItem.connection_id == conn.id, SchemaItem.hidden)
        )
    }
    engine = get_engine(decrypt(conn.dsn_encrypted))
    try:
        tables = await run_in_threadpool(
            scan_schema, engine, conn.dialect, list(conn.schema_allowlist or []) or None, hidden
        )
    except Exception as e:  # noqa: BLE001 - report any driver error to the admin
        conn.status, conn.status_detail = "error", f"Scan failed: {str(e).splitlines()[0]}"
        await session.commit()
        raise ScanFailed(conn.status_detail) from None
    counts = await save_scan(session, conn, tables, get_embedder())
    await session.refresh(conn)
    return counts
