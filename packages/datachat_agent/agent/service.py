"""Answer a question and record it in the query log: shared by the library and the MCP server."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.agent.graph import AgentDeps, AgentResult, run_agent
from datachat_agent.agent.sessions import get_or_create_session, recent_turns
from datachat_agent.config import get_settings
from datachat_agent.core.pii import PiiMasker
from datachat_agent.server.models import ChatSession, Connection, QueryLog


async def answer_and_log(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    connection: Connection | None,
    question: str,
    llm,
    embedder,
    session_key: str | None = None,
    user_context: dict[str, Any] | None = None,
    api_key_id: uuid.UUID | None = None,
) -> tuple[AgentResult, ChatSession, QueryLog]:
    if connection is not None and (connection.status != "ok" or connection.last_scanned_at is None):
        connection = None  # not ready yet: documents only
    chat = await get_or_create_session(session, tenant_id, session_key, None)
    history = await recent_turns(session, chat.id)
    result = await run_agent(
        question,
        AgentDeps(
            session=session,
            llm=llm,
            embedder=embedder,
            tenant_id=tenant_id,
            connection=connection,
            user_context=dict(user_context or {}),
            history=history,
            masker=PiiMasker() if get_settings().mask_pii else None,
        ),
    )
    data = result.sql.result if result.sql else None
    log = QueryLog(
        tenant_id=tenant_id,
        session_id=chat.id,
        connection_id=connection.id if connection else None,
        api_key_id=api_key_id,
        question=question,
        route=result.route,
        sql=result.sql.sql if result.sql else None,
        row_count=data.row_count if data else None,
        chunks_used=[c["chunk_id"] for c in result.citations if c.get("chunk_id")],
        answer=None if result.llm_unavailable else result.answer,
        latency_ms=result.latency_ms,
        tokens=result.tokens,
        error=result.error,
    )
    session.add(log)
    await session.commit()
    return result, chat, log
