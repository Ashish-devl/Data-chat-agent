"""Abuse and cost limits: requests per minute per API key, and tokens per month per tenant."""

import time
import uuid
from datetime import UTC, datetime
from functools import lru_cache

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.config import get_settings
from datachat_agent.server.models import QueryLog

WINDOW_SECONDS = 60


class MemoryLimiter:
    """Fixed one-minute windows counted in this process. Right for a single server."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, int], int] = {}

    async def hit(self, key: str, window: int) -> int:
        if len(self._counts) > 10_000:  # drop old windows now and then
            self._counts = {k: v for k, v in self._counts.items() if k[1] >= window - 1}
        self._counts[(key, window)] = self._counts.get((key, window), 0) + 1
        return self._counts[(key, window)]


class RedisLimiter:
    """The same windows kept in Redis, so every API server shares one count per key."""

    def __init__(self, url: str) -> None:
        from redis.asyncio import Redis

        self._redis = Redis.from_url(url)

    async def hit(self, key: str, window: int) -> int:
        name = f"datachat:rl:{key}:{window}"
        pipe = self._redis.pipeline()
        pipe.incr(name)
        pipe.expire(name, WINDOW_SECONDS * 2)
        count, _ = await pipe.execute()
        return int(count)


@lru_cache
def _limiter(backend: str, url: str):
    return RedisLimiter(url) if backend == "redis" else MemoryLimiter()


def reset_memory_limiter() -> None:
    _limiter.cache_clear()


async def enforce_rate_limit(key_id: uuid.UUID, limit_per_minute: int | None) -> None:
    settings = get_settings()
    if settings.rate_limit_backend == "off":
        return
    limit = limit_per_minute or settings.default_rate_limit_per_minute
    now = time.time()
    window = int(now // WINDOW_SECONDS)
    count = await _limiter(settings.rate_limit_backend, settings.redis_url).hit(str(key_id), window)
    if count > limit:
        retry_after = max(1, int((window + 1) * WINDOW_SECONDS - now))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate limit of {limit} requests per minute reached for this API key.",
            headers={"Retry-After": str(retry_after), "X-RateLimit-Limit": str(limit)},
        )


def month_start() -> datetime:
    return datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def tokens_this_month(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    used = await session.scalar(
        select(func.coalesce(func.sum(QueryLog.tokens), 0)).where(
            QueryLog.tenant_id == tenant_id, QueryLog.created_at >= month_start()
        )
    )
    return int(used or 0)


def monthly_limit(tenant_limit: int | None) -> int | None:
    return tenant_limit if tenant_limit is not None else get_settings().default_monthly_token_limit


async def enforce_token_budget(
    session: AsyncSession, tenant_id: uuid.UUID, tenant_limit: int | None
) -> None:
    limit = monthly_limit(tenant_limit)
    if limit is None:
        return
    if await tokens_this_month(session, tenant_id) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"This account has used its monthly limit of {limit:,} model tokens.",
        )
