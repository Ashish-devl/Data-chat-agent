from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.config import get_settings
from datachat_agent.security import Scope
from datachat_agent.server.auth import Principal, require_scope
from datachat_agent.server.db import get_session
from datachat_agent.server.limits import month_start, monthly_limit
from datachat_agent.server.models import QueryLog, Tenant

router = APIRouter(prefix="/usage", tags=["usage"])


class Usage(BaseModel):
    period_start: datetime
    requests: int
    tokens: int
    monthly_token_limit: int | None
    tokens_remaining: int | None
    rate_limit_per_minute: int


@router.get("", response_model=Usage)
async def usage(
    principal: Principal = Depends(require_scope(Scope.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> Usage:
    """Questions and model tokens this calendar month, with this account's limits."""
    start = month_start()
    requests, tokens = (
        await session.execute(
            select(func.count(QueryLog.id), func.coalesce(func.sum(QueryLog.tokens), 0)).where(
                QueryLog.tenant_id == principal.tenant_id, QueryLog.created_at >= start
            )
        )
    ).one()
    tenant = await session.get(Tenant, principal.tenant_id)
    limit = monthly_limit(tenant.monthly_token_limit)
    return Usage(
        period_start=start,
        requests=requests,
        tokens=tokens,
        monthly_token_limit=limit,
        tokens_remaining=max(0, limit - tokens) if limit is not None else None,
        rate_limit_per_minute=tenant.rate_limit_per_minute
        or get_settings().default_rate_limit_per_minute,
    )
