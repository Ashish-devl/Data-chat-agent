import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.security import Scope
from datachat_agent.server.auth import Principal, require_scope
from datachat_agent.server.db import get_session
from datachat_agent.server.models import QueryLog

router = APIRouter(tags=["feedback"])


class FeedbackIn(BaseModel):
    query_id: uuid.UUID
    rating: Literal["helpful", "wrong"]
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackOut(BaseModel):
    query_id: uuid.UUID
    rating: str
    comment: str | None
    recorded_at: datetime


@router.post("/feedback", response_model=FeedbackOut)
async def give_feedback(
    body: FeedbackIn,
    principal: Principal = Depends(require_scope(Scope.ASK)),
    session: AsyncSession = Depends(get_session),
) -> FeedbackOut:
    """Mark an answer helpful or wrong. Sending again replaces the earlier rating."""
    log = await session.scalar(
        select(QueryLog).where(
            QueryLog.id == body.query_id, QueryLog.tenant_id == principal.tenant_id
        )
    )
    if log is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found")
    log.feedback = body.rating
    log.feedback_comment = (body.comment or "").strip() or None
    log.feedback_at = datetime.now(UTC)
    await session.commit()
    return FeedbackOut(
        query_id=log.id,
        rating=log.feedback,
        comment=log.feedback_comment,
        recorded_at=log.feedback_at,
    )
