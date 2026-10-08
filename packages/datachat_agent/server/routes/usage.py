from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.security import Scope
from datachat_agent.server.auth import Principal, require_scope
from datachat_agent.server.db import get_session
from datachat_agent.server.models import QueryLog

router = APIRouter(prefix="/usage", tags=["usage"])


class Usage(BaseModel):
    period_start: datetime
    requests: int
    tokens: int


@router.get("", response_model=Usage)
async def usage(
    principal: Principal = Depends(require_scope(Scope.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> Usage:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    requests, tokens = (
        await session.execute(
            select(func.count(QueryLog.id), func.coalesce(func.sum(QueryLog.tokens), 0)).where(
                QueryLog.tenant_id == principal.tenant_id, QueryLog.created_at >= start
            )
        )
    ).one()
    return Usage(period_start=start, requests=requests, tokens=tokens)
