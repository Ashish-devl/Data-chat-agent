import json
import time
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.agent.answer_plan import plan_answer
from datachat_agent.agent.graph import AgentDeps, AgentResult, gather, history_answers, run_agent
from datachat_agent.agent.sessions import get_or_create_session, recent_turns
from datachat_agent.config import get_settings
from datachat_agent.core.embeddings import Embedder, get_embedder
from datachat_agent.core.llm import StreamUsage, get_chat_model
from datachat_agent.core.pii import PiiMasker, StreamUnmasker
from datachat_agent.security import Scope
from datachat_agent.server.auth import Principal, require_scope
from datachat_agent.server.db import get_session, get_sessionmaker
from datachat_agent.server.limits import enforce_token_budget
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


def _masker() -> PiiMasker | None:
    return PiiMasker() if get_settings().mask_pii else None


def _log_row(principal: Principal, chat_id, connection, question: str, state: dict) -> QueryLog:
    sql = state.get("sql")
    data = sql.result if sql else None
    return QueryLog(
        tenant_id=principal.tenant_id,
        session_id=chat_id,
        connection_id=connection.id if connection else None,
        api_key_id=principal.key_id,
        question=question,
        route=state.get("route"),
        sql=sql.sql if sql else None,
        row_count=data.row_count if data else None,
        chunks_used=[c.get("chunk_id") for c in state.get("citations") or [] if c.get("chunk_id")],
        answer=None if state.get("llm_unavailable") else state.get("answer"),
        latency_ms=state.get("latency_ms"),
        tokens=state.get("tokens", 0),
        error=state.get("error"),
    )


def _response(log: QueryLog, client_key: str, state: dict, include_rows: bool) -> AskResponse:
    sql = state.get("sql")
    data = sql.result if sql else None
    chart = state.get("chart")
    return AskResponse(
        query_id=log.id,
        session_id=client_key,
        answer=state.get("answer") or "",
        route=state.get("route") or "none",
        sql=sql.sql if sql and data else None,
        columns=data.columns if data and include_rows else [],
        rows=data.rows if data and include_rows else [],
        row_count=data.row_count if data else 0,
        truncated=data.truncated if data else False,
        citations=state.get("citations") or [],
        chart=Chart(**chart) if chart else None,
        latency_ms=state.get("latency_ms") or 0,
        tokens=state.get("tokens", 0),
        error=state.get("error"),
    )


async def _prepare(body: AskRequest, principal: Principal, session: AsyncSession):
    await enforce_token_budget(session, principal.tenant_id, principal.monthly_token_limit)
    connection = await _pick_connection(body, principal, session)
    chat = await get_or_create_session(session, principal.tenant_id, body.session_id, body.user_ref)
    history = await recent_turns(session, chat.id)
    return connection, chat, history


@router.post("/ask", response_model=AskResponse)
async def ask(
    body: AskRequest,
    principal: Principal = Depends(asker),
    session: AsyncSession = Depends(get_session),
    llm=Depends(llm_dependency),
    embedder: Embedder = Depends(get_embedder),
):
    connection, chat, history = await _prepare(body, principal, session)
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
            masker=_masker(),
        ),
    )
    state = {
        "route": result.route,
        "answer": result.answer,
        "sql": result.sql,
        "chart": result.chart,
        "citations": result.citations,
        "tokens": result.tokens,
        "error": result.error,
        "llm_unavailable": result.llm_unavailable,
        "latency_ms": result.latency_ms,
    }
    log = _log_row(principal, chat.id, connection, body.question, state)
    session.add(log)
    await session.commit()
    response = _response(log, chat.client_key, state, body.include_rows)
    if result.llm_unavailable:
        return JSONResponse(status_code=503, content=response.model_dump(mode="json"))
    return response


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post(
    "/ask/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def ask_stream(
    body: AskRequest,
    principal: Principal = Depends(asker),
    session: AsyncSession = Depends(get_session),
    llm=Depends(llm_dependency),
    embedder: Embedder = Depends(get_embedder),
):
    """Same as /ask, as Server-Sent Events: `meta`, then `data` (SQL and rows) if any, then
    `token` events with the answer as it is written, then `done` with the full /ask response."""
    connection, chat, history = await _prepare(body, principal, session)
    connection_id = connection.id if connection else None
    chat_id, client_key = chat.id, chat.client_key

    async def events():
        started = time.perf_counter()
        masker = _masker()
        state: dict[str, Any] = {}
        # The request's own database session closes when the response starts; use a new one.
        async with get_sessionmaker()() as db:
            conn = await db.get(Connection, connection_id) if connection_id else None
            deps = AgentDeps(
                session=db,
                llm=llm,
                embedder=embedder,
                tenant_id=principal.tenant_id,
                connection=conn,
                user_context=dict(body.user_context),
                history=history,
                masker=masker,
            )
            try:
                state = dict(await gather(body.question, deps))
                yield _sse("meta", {"session_id": client_key, "route": state["route"]})
                sql = state.get("sql")
                if sql is not None and sql.result is not None:
                    r = sql.result
                    yield _sse(
                        "data",
                        {
                            "sql": sql.sql,
                            "columns": r.columns if body.include_rows else [],
                            "rows": r.rows if body.include_rows else [],
                            "row_count": r.row_count,
                            "truncated": r.truncated,
                        },
                    )

                plan = plan_answer(state, history_answers(deps), masker)
                if plan.fixed is not None:
                    update = dict(plan.fixed)
                    yield _sse("token", {"text": update["answer"]})
                else:
                    update, parts = None, []
                    usage_count = StreamUsage(get_settings().llm_provider)
                    unmasker = StreamUnmasker(masker)
                    try:
                        async for chunk in llm.astream(plan.messages):
                            piece = chunk.content if isinstance(chunk.content, str) else ""
                            usage_count.update(getattr(chunk, "usage_metadata", None))
                            parts.append(piece)
                            visible = unmasker.feed(piece)
                            if visible:
                                yield _sse("token", {"text": visible})
                        tail = unmasker.flush()
                        if tail:
                            yield _sse("token", {"text": tail})
                        text = "".join(parts)
                        update = plan.finalize(
                            masker.unmask(text) if masker else text, usage_count.total
                        )
                    except Exception as e:  # noqa: BLE001 - finish with the fallback answer
                        update = plan.on_error(e)
                        if not parts:
                            yield _sse("token", {"text": update["answer"]})
                state["tokens"] = state.get("tokens", 0) + update.pop("tokens_used", 0)
                update.pop("fallback", None)
                state.update(update)
            except Exception:  # noqa: BLE001
                state.setdefault("route", "none")
                state["answer"] = state.get("answer") or ""
                state["error"] = "Something went wrong while answering."
                yield _sse("error", {"message": state["error"]})
            state["latency_ms"] = int((time.perf_counter() - started) * 1000)
            log = _log_row(principal, chat_id, conn, body.question, state)
            db.add(log)
            await db.commit()
            yield _sse(
                "done", _response(log, client_key, state, body.include_rows).model_dump(mode="json")
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
