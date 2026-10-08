from fastapi import APIRouter, FastAPI

from datachat_agent import __version__
from datachat_agent.server.routes import health, keys, usage

# Include-able in any FastAPI app: host_app.include_router(router, prefix="/ai")
router = APIRouter(prefix="/v1")
router.include_router(health.router)
router.include_router(keys.router)
router.include_router(usage.router)


def create_app() -> FastAPI:
    app = FastAPI(title="DataChat Agent", version=__version__)
    app.include_router(router)
    return app


app = create_app()
