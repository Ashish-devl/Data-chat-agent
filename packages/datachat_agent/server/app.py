from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from datachat_agent import __version__
from datachat_agent.security import check_secret_key
from datachat_agent.server.db import dispose_engine
from datachat_agent.server.jobs import wait_for_inline_jobs
from datachat_agent.server.routes import (
    ask,
    connections,
    documents,
    health,
    keys,
    knowledge,
    schema_items,
    usage,
)

# Include-able in any FastAPI app: host_app.include_router(router, prefix="/ai")
router = APIRouter(prefix="/v1")
router.include_router(health.router)
router.include_router(keys.router)
router.include_router(usage.router)
router.include_router(connections.router)
router.include_router(schema_items.router)
router.include_router(knowledge.router)
router.include_router(documents.router)
router.include_router(ask.router)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    check_secret_key()
    yield
    await wait_for_inline_jobs()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="DataChat Agent", version=__version__, lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
