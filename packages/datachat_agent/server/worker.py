"""The arq worker: `datachat-agent worker` (needs JOBS_BACKEND=arq and Redis)."""

import uuid

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from datachat_agent.config import get_settings
from datachat_agent.server import jobs


async def ingest_document(ctx, document_id: str) -> None:
    await jobs.run_ingest(uuid.UUID(document_id))


async def scan_connection(ctx, connection_id: str) -> None:
    await jobs.run_scan(uuid.UUID(connection_id))


async def nightly_rescan(ctx) -> int:
    """Re-read every working connection's schema so new tables and columns show up."""
    from datachat_agent.server.db import get_sessionmaker
    from datachat_agent.server.models import Connection

    async with get_sessionmaker()() as session:
        ids = list(
            await session.scalars(
                select(Connection.id).where(
                    Connection.status == "ok", Connection.last_scanned_at.is_not(None)
                )
            )
        )
    for connection_id in ids:
        await jobs.run_scan(connection_id)
    return len(ids)


class WorkerSettings:
    functions = [ingest_document, scan_connection]
    cron_jobs = [cron(nightly_rescan, hour=2, minute=0, run_at_startup=False)]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 600
