"""Conversation threads: the calling app names a session; follow-ups see the last few turns."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.server.models import ChatSession, QueryLog

HISTORY_TURNS = 4


@dataclass
class Turn:
    question: str
    sql: str | None
    answer: str | None


async def get_or_create_session(
    session: AsyncSession, tenant_id: uuid.UUID, client_key: str | None, user_ref: str | None
) -> ChatSession:
    key = client_key or uuid.uuid4().hex
    existing = await session.scalar(
        select(ChatSession).where(ChatSession.tenant_id == tenant_id, ChatSession.client_key == key)
    )
    if existing is not None:
        return existing
    chat = ChatSession(tenant_id=tenant_id, client_key=key, user_ref=user_ref)
    session.add(chat)
    try:
        await session.commit()
    except IntegrityError:  # another request created it at the same moment
        await session.rollback()
        return await session.scalar(
            select(ChatSession).where(
                ChatSession.tenant_id == tenant_id, ChatSession.client_key == key
            )
        )
    return chat


async def recent_turns(session: AsyncSession, chat_id: uuid.UUID, limit: int = HISTORY_TURNS):
    rows = await session.scalars(
        select(QueryLog)
        .where(QueryLog.session_id == chat_id, QueryLog.answer.is_not(None))
        .order_by(QueryLog.created_at.desc())
        .limit(limit)
    )
    return [Turn(r.question, r.sql, r.answer) for r in reversed(list(rows))]
