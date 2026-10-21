import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.agent.graph import AgentDeps, AgentResult, run_agent
from datachat_agent.agent.sessions import get_or_create_session, recent_turns
from datachat_agent.core.embeddings import Embedder, get_embedder
from datachat_agent.core.llm import get_chat_model
from datachat_agent.security import Scope
from datachat_agent.server.auth import Principal, require_scope
from datachat_agent.server.db import get_session
from datachat_agent.server.models import ChatSession, Connection, QueryLog

router = APIRouter(tags=["ask"])
asker = require_scope(Scope.ASK)

ContextValue = str | int | float


def llm_dependency():
    """Overridable in tests and by host apps that bring their own model."""
    return get_chat_model()


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    connection_id: uuid.UUID | None = Field(
        default=None, description="Optional when the tenant has exactly one ready connection"
    )
    session_id: str | None = Field(
        default=None,
        max_length=100,
        pattern=r"^[A-Za-z0-9_.:\-]+$",
        description="Your id for the conversation; follow-ups with the same id see earlier turns",
    )
    user_ref: str | None = Field(default=None, max_length=200)
    user_context: dict[str, ContextValue] = Field(
        default_factory=dict,
        description='Values for row filters, e.g. {"user_company": 7}',
    )
    include_rows: bool = True

    @field_validator("user_context")
    @classmethod
    def small_context(cls, v: dict[str, ContextValue]) -> dict[str, ContextValue]:
        if len(v) > 20:
            raise ValueError("At most 20 user_context values")
        for key, value in v.items():
            if len(key) > 64 or (isinstance(value, str) and len(value) > 500):
                raise ValueError("user_context keys ≤ 64 chars, values ≤ 500 chars")
        return v


class Chart(BaseModel):
    type: str
    x: str
    y: str


class AskResponse(BaseModel):
    query_id: uuid.UUID
    session_id: str
    answer: str
    route: str
    sql: str | None
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    row_count: int = 0
    truncated: bool = False
    citations: list[dict[str, Any]] = []
    chart: Chart | None = None
    latency_ms: int
    tokens: int
    error: str | None = None


async def _pick_connection(
    body: AskRequest, principal: Principal, session: AsyncSession
) -> Connection | None:
    if body.connection_id is not None:
        conn = await session.scalar(
            select(Connection).where(
                Connection.id == body.connection_id, Connection.tenant_id == principal.tenant_id
            )
        )
        if conn is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Connection not found")
        if conn.status != "ok" or conn.last_scanned_at is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Connection is not ready: test and scan it first"
            )
        return conn
    ready = list(
        await session.scalars(
            select(Connection).where(
                Connection.tenant_id == principal.tenant_id,
                Connection.status == "ok",
                Connection.last_scanned_at.is_not(None),
            )
        )
    )
    if len(ready) > 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This account has several connections; pass connection_id",
        )
    return ready[0] if ready else None


@router.post("/ask", response_model=AskResponse)
async def ask(
    body: AskRequest,
    principal: Principal = Depends(asker),
    session: AsyncSession = Depends(get_session),
    llm=Depends(llm_dependency),
    embedder: Embedder = Depends(get_embedder),
):
    connection = await _pick_connection(body, principal, session)
    chat = await get_or_create_session(session, principal.tenant_id, body.session_id, body.user_ref)
    history = await recent_turns(session, chat.id)

    result: AgentResult = await run_agent(
        body.question,
        AgentDeps(
            session=session,
            llm=llm,
            embedder=embedder,
            tenant_id=principal.tenant_id,
            connection=connection,
            user_context=dict(body.user_context),
            history=history,
        ),
    )
    sql = result.sql
    data = sql.result if sql else None

    log = QueryLog(
        tenant_id=principal.tenant_id,
        session_id=chat.id,
        connection_id=connection.id if connection else None,
        api_key_id=principal.key_id,
        question=body.question,
        route=result.route,
        sql=sql.sql if sql else None,
        row_count=data.row_count if data else None,
        chunks_used=[c.get("chunk_id") for c in result.citations if c.get("chunk_id")],
        answer=None if result.llm_unavailable else result.answer,
        latency_ms=result.latency_ms,
        tokens=result.tokens,
        error=result.error,
    )
    session.add(log)
    await session.commit()

    response = AskResponse(
        query_id=log.id,
        session_id=chat.client_key,
        answer=result.answer,
        route=result.route,
        sql=sql.sql if sql and data else None,
        columns=data.columns if data and body.include_rows else [],
        rows=data.rows if data and body.include_rows else [],
        row_count=data.row_count if data else 0,
        truncated=data.truncated if data else False,
        citations=result.citations,
        chart=Chart(**result.chart) if result.chart else None,
        latency_ms=result.latency_ms,
        tokens=result.tokens,
        error=result.error,
    )
    if result.llm_unavailable:
        return JSONResponse(status_code=503, content=response.model_dump(mode="json"))
    return response


class TurnOut(BaseModel):
    query_id: uuid.UUID
    question: str
    answer: str | None
    route: str | None
    sql: str | None
    row_count: int | None
    error: str | None
    created_at: datetime


class SessionOut(BaseModel):
    session_id: str
    user_ref: str | None
    created_at: datetime
    turns: list[TurnOut]


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_session_history(
    session_id: str,
    principal: Principal = Depends(asker),
    session: AsyncSession = Depends(get_session),
) -> SessionOut:
    chat = await session.scalar(
        select(ChatSession).where(
            ChatSession.tenant_id == principal.tenant_id, ChatSession.client_key == session_id
        )
    )
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    logs = await session.scalars(
        select(QueryLog).where(QueryLog.session_id == chat.id).order_by(QueryLog.created_at)
    )
    return SessionOut(
        session_id=chat.client_key,
        user_ref=chat.user_ref,
        created_at=chat.created_at,
        turns=[
            TurnOut(
                query_id=q.id,
                question=q.question,
                answer=q.answer,
                route=q.route,
                sql=q.sql,
                row_count=q.row_count,
                error=q.error,
                created_at=q.created_at,
            )
            for q in logs
        ],
    )
