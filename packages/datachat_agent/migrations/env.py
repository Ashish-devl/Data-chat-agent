import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from datachat_agent.config import get_settings
from datachat_agent.server.models import Base

target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    url = context.config.attributes.get("database_url") or get_settings().database_url
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
        await conn.commit()
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a database.")

asyncio.run(_run_async())
