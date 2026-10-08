from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from datachat_agent.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = _sessionmaker = None


def alembic_config(database_url: str | None = None):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["database_url"] = database_url or get_settings().database_url
    return cfg


def run_migrations(database_url: str | None = None, revision: str = "head") -> None:
    """Bring the database schema up to date. Safe to run more than once. Not for async code."""
    from alembic import command

    command.upgrade(alembic_config(database_url), revision)
