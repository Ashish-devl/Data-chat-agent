from fastapi import APIRouter

from datachat_agent import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness check: the server is up. No key needed."""
    return {"status": "ok", "version": __version__}
