"""The agent: route -> (database and/or documents) -> answer, as a LangGraph graph."""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from datachat_agent.agent.answer import I_DONT_KNOW, chart_hint, compose_answer
from datachat_agent.agent.knowledge import Knowledge, load_knowledge
from datachat_agent.agent.router import Route, choose_route
from datachat_agent.agent.sessions import Turn
from datachat_agent.core.embeddings import Embedder
from datachat_agent.server.models import Connection, Document, SchemaItem
from datachat_agent.sql.pipeline import SQLAnswer, answer_with_sql


@dataclass
class AgentDeps:
    session: AsyncSession
    llm: BaseChatModel | Runnable
    embedder: Embedder
    tenant_id: uuid.UUID
    connection: Connection | None
    user_context: dict[str, Any] = field(default_factory=dict)
    history: list[Turn] = field(default_factory=list)


class AgentState(TypedDict, total=False):
    question: str
    route: Route
    sql: SQLAnswer | None
    documents: list[dict[str, Any]]
    answer: str
    chart: dict[str, str] | None
    citations: list[dict[str, Any]]
    tokens: int
    error: str | None
    llm_unavailable: bool


@dataclass
class AgentResult:
    question: str
    route: Route
    answer: str
    sql: SQLAnswer | None
    chart: dict[str, str] | None
    citations: list[dict[str, Any]]
    tokens: int
    error: str | None
    llm_unavailable: bool
    latency_ms: int


def _deps(config: RunnableConfig) -> AgentDeps:
    return config["configurable"]["deps"]


async def _has_documents(deps: AgentDeps) -> bool:
    count = await deps.session.scalar(
        select(func.count(Document.id)).where(
            Document.tenant_id == deps.tenant_id, Document.status == "ready"
        )
    )
    return bool(count)


async def _table_names(deps: AgentDeps) -> list[str]:
    if deps.connection is None:
        return []
    rows = await deps.session.scalars(
        select(SchemaItem.table_name).where(
            SchemaItem.connection_id == deps.connection.id,
            SchemaItem.column_name.is_(None),
            SchemaItem.hidden.is_(False),
        )
    )
    return list(rows)


async def route_node(state: AgentState, config: RunnableConfig) -> AgentState:
    deps = _deps(config)
    has_db = deps.connection is not None
    has_docs = await _has_documents(deps)
    tables = await _table_names(deps) if has_db and has_docs else None
    route, tokens = await choose_route(deps.llm, state["question"], has_db, has_docs, tables)
    return {"route": route, "tokens": state.get("tokens", 0) + tokens}


async def database_node(state: AgentState, config: RunnableConfig) -> AgentState:
    deps = _deps(config)
    question = state["question"]
    previous = deps.history[-1].question if deps.history else ""
    base_text = f"{previous}\n{question}" if previous else question

    vector = await run_in_threadpool(deps.embedder.embed_query, base_text)
    knowledge: Knowledge = await load_knowledge(deps.session, deps.connection.id, question, vector)
    # Business terms named in the question point retrieval at the right tables.
    retrieval_text = "\n".join([base_text, *knowledge.matched_definitions])

    result = await answer_with_sql(
        deps.session,
        deps.connection,
        question,
        deps.llm,
        deps.embedder,
        user_context=deps.user_context,
        business_terms=knowledge.terms,
        examples=knowledge.examples,
        history=[(t.question, t.sql) for t in deps.history if t.sql],
        retrieval_text=retrieval_text,
    )
    return {
        "sql": result,
        "tokens": state.get("tokens", 0) + result.tokens,
        "llm_unavailable": result.llm_unavailable,
    }


async def documents_node(state: AgentState, config: RunnableConfig) -> AgentState:
    # Week 4 fills this in: hybrid search over PDF chunks and line items.
    return {"documents": [], "citations": []}


async def answer_node(state: AgentState, config: RunnableConfig) -> AgentState:
    deps = _deps(config)
    sql: SQLAnswer | None = state.get("sql")
    tokens = state.get("tokens", 0)

    if state["route"] == "none":
        return {
            "answer": "No data source is connected yet, so I can't answer that.",
            "chart": None,
            "error": None,
        }
    if state.get("llm_unavailable"):
        return {
            "answer": sql.error if sql else "The language model is unavailable.",
            "chart": None,
            "error": sql.error if sql else None,
        }

    if sql is not None and sql.result is not None:
        r = sql.result
        history = [(t.question, t.answer) for t in deps.history if t.answer]
        composed = await compose_answer(
            deps.llm, state["question"], r.columns, r.rows, r.row_count, r.truncated, history
        )
        return {
            "answer": composed.text,
            "chart": chart_hint(r.columns, r.rows),
            "tokens": tokens + composed.tokens,
            "error": None,
        }

    if sql is not None and sql.cannot_answer and not state.get("documents"):
        return {"answer": I_DONT_KNOW, "chart": None, "error": None}
    if sql is not None and sql.error:
        return {
            "answer": "I couldn't get that data: " + sql.error,
            "chart": None,
            "error": sql.error,
        }
    return {"answer": I_DONT_KNOW, "chart": None, "error": None}


def _after_route(state: AgentState) -> str:
    return {"database": "database", "both": "database", "documents": "documents"}.get(
        state["route"], "answer"
    )


def _after_database(state: AgentState) -> str:
    return "documents" if state["route"] == "both" else "answer"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("route", route_node)
    g.add_node("database", database_node)
    g.add_node("documents", documents_node)
    g.add_node("answer", answer_node)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", _after_route, ["database", "documents", "answer"])
    g.add_conditional_edges("database", _after_database, ["documents", "answer"])
    g.add_edge("documents", "answer")
    g.add_edge("answer", END)
    return g.compile()


_GRAPH = build_graph()


async def run_agent(question: str, deps: AgentDeps) -> AgentResult:
    started = time.perf_counter()
    state: AgentState = await _GRAPH.ainvoke(
        {"question": question, "tokens": 0}, config={"configurable": {"deps": deps}}
    )
    return AgentResult(
        question=question,
        route=state["route"],
        answer=state["answer"],
        sql=state.get("sql"),
        chart=state.get("chart"),
        citations=state.get("citations", []),
        tokens=state.get("tokens", 0),
        error=state.get("error"),
        llm_unavailable=bool(state.get("llm_unavailable")),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
