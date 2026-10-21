"""Background work: document ingestion and schema scans.

JOBS_BACKEND decides where it runs: sync (inside the request), inline (a background task in the
API process) or arq (a separate `datachat-agent worker` process fed through Redis).
"""

import asyncio
import logging
import uuid

from datachat_agent.config import get_settings

log = logging.getLogger("datachat_agent.jobs")
_tasks: set[asyncio.Task] = set()
_arq_pool = None


def _llm_or_none():
    """Ingestion uses the model only for odd tables; without one configured it still works."""
    from datachat_agent.core.llm import get_chat_model

    try:
        return get_chat_model()
    except RuntimeError:
        return None


async def run_ingest(document_id: uuid.UUID) -> None:
    from datachat_agent.core.embeddings import get_embedder
    from datachat_agent.docs.ingest import ingest_document
    from datachat_agent.server.db import get_sessionmaker

    async with get_sessionmaker()() as session:
        await ingest_document(session, document_id, get_embedder(), _llm_or_none())


async def run_scan(connection_id: uuid.UUID) -> None:
    from datachat_agent.server.db import get_sessionmaker
    from datachat_agent.server.models import Connection
    from datachat_agent.server.services import ScanFailed, ScanRefused, scan_connection

    async with get_sessionmaker()() as session:
        conn = await session.get(Connection, connection_id)
        if conn is None:
            return
        try:
            await scan_connection(session, conn)
        except (ScanRefused, ScanFailed) as e:
            log.warning("Scan of %s failed: %s", connection_id, e)


async def _arq():
    global _arq_pool
    if _arq_pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings

        _arq_pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _arq_pool


async def _dispatch(name: str, func, target_id: uuid.UUID) -> None:
    backend = get_settings().jobs_backend
    if backend == "sync":
        await func(target_id)
    elif backend == "inline":
        task = asyncio.create_task(func(target_id))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    else:
        pool = await _arq()
        await pool.enqueue_job(name, str(target_id), _job_id=f"{name}:{target_id}")


async def enqueue_ingest(document_id: uuid.UUID) -> None:
    await _dispatch("ingest_document", run_ingest, document_id)


async def enqueue_scan(connection_id: uuid.UUID) -> None:
    await _dispatch("scan_connection", run_scan, connection_id)


async def wait_for_inline_jobs() -> None:
    """Used on shutdown and in tests: let background tasks finish."""
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
