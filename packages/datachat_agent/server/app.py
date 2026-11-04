from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from datachat_agent import __version__
from datachat_agent.config import get_settings
from datachat_agent.security import check_secret_key
from datachat_agent.server.db import dispose_engine
from datachat_agent.server.jobs import wait_for_inline_jobs
from datachat_agent.server.routes import (
    ask,
    connections,
    documents,
    feedback,
    health,
    keys,
    knowledge,
    schema_items,
    usage,
    users,
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
router.include_router(feedback.router)
router.include_router(users.router)


STATIC_DIR = Path(__file__).resolve().parent / "static"

# Served outside /v1: the chat widget's script tag points here.
static_router = APIRouter()


@static_router.get("/widget.js", include_in_schema=False)
async def widget_js() -> Response:
    """The chat widget (built from widget/). Public: it holds no secrets."""
    return Response(
        (STATIC_DIR / "widget.js").read_bytes(),
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=3600"},
    )


CONSOLE_DIR = STATIC_DIR / "console"
_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


@static_router.get("/console", include_in_schema=False)
async def console_root(request: Request) -> RedirectResponse:
    return RedirectResponse(str(request.url.replace(path=request.url.path + "/")))


@static_router.get("/console/{path:path}", include_in_schema=False)
async def console_files(path: str) -> Response:
    """The admin console (built from console/). Unknown paths get index.html (one-page app)."""
    root = CONSOLE_DIR.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        target = root / "index.html"
    cache = "no-cache" if target.name == "index.html" else "public, max-age=31536000, immutable"
    return Response(
        target.read_bytes(),
        media_type=_TYPES.get(target.suffix, "application/octet-stream"),
        headers={"Cache-Control": cache},
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    check_secret_key()
    yield
    await wait_for_inline_jobs()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="DataChat Agent", version=__version__, lifespan=lifespan)
    origins = [o.strip() for o in get_settings().cors_allow_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
    app.include_router(router)
    app.include_router(static_router)
    return app


app = create_app()


def mount(app: FastAPI, prefix: str = "") -> None:
    """Add DataChat Agent's API to your own FastAPI app, e.g. mount(app, prefix="/ai")
    serves /ai/v1/ask. Run `datachat-agent init-db` once to create its tables."""
    check_secret_key()
    app.include_router(router, prefix=prefix)
    app.include_router(static_router, prefix=prefix)
