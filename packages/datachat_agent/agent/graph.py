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

from datachat_agent.agent.answer_plan import plan_answer, run_plan
from datachat_agent.agent.doc_planner import plan_items
from datachat_agent.agent.knowledge import Knowledge, load_knowledge
from datachat_agent.agent.router import Route, choose_route
from datachat_agent.agent.sessions import Turn
from datachat_agent.core.embeddings import Embedder
from datachat_agent.core.pii import PiiMasker
from datachat_agent.docs.search import ItemRow, Passage, find_items, search_passages
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
    masker: PiiMasker | None = None


class AgentState(TypedDict, total=False):
    question: str
    route: Route
    sql: SQLAnswer | None
    documents: list[Passage]
    items: list[ItemRow]
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
        partial=state.get("route") == "both",
        masker=deps.masker,
    )
    return {
        "sql": result,
        "tokens": state.get("tokens", 0) + result.tokens,
        "llm_unavailable": result.llm_unavailable,
    }


async def documents_node(state: AgentState, config: RunnableConfig) -> AgentState:
    """Hybrid passage search, plus line items when the question needs table numbers."""
    deps = _deps(config)
    question = state["question"]
    previous = deps.history[-1].question if deps.history else ""
    search_text = f"{previous}\n{question}" if previous else question
    vector = await run_in_threadpool(deps.embedder.embed_query, search_text)
    passages = await search_passages(deps.session, deps.tenant_id, search_text, vector)

    plan = await plan_items(deps.llm, question)
    items = []
    if plan.need_items:
        items = await find_items(deps.session, deps.tenant_id, plan.document, plan.item)
        if not items and plan.document and plan.item:
            items = await find_items(deps.session, deps.tenant_id, document=plan.document)
        if not items and plan.item:
            items = await find_items(deps.session, deps.tenant_id, item=plan.item)
    return {
        "documents": passages,
        "items": items,
        "tokens": state.get("tokens", 0) + plan.tokens,
    }


def history_answers(deps: AgentDeps) -> list[tuple[str, str]]:
    return [(t.question, t.answer) for t in deps.history if t.answer]


async def answer_node(state: AgentState, config: RunnableConfig) -> AgentState:
    deps = _deps(config)
    plan = plan_answer(state, history_answers(deps), deps.masker)
    update = await run_plan(plan, deps.llm, deps.masker)
    tokens = state.get("tokens", 0) + update.pop("tokens_used", 0)
    update.pop("fallback", None)
    return {**update, "tokens": tokens}


async def _no_answer(state: AgentState, config: RunnableConfig) -> AgentState:
    return {}


def _after_route(state: AgentState) -> str:
    return {"database": "database", "both": "database", "documents": "documents"}.get(
        state["route"], "answer"
    )


def _after_database(state: AgentState) -> str:
    return "documents" if state["route"] == "both" else "answer"


def build_graph(with_answer: bool = True):
    """with_answer=False stops after gathering data, for the streaming endpoint."""
    g = StateGraph(AgentState)
    g.add_node("route", route_node)
    g.add_node("database", database_node)
    g.add_node("documents", documents_node)
    g.add_node("answer", answer_node if with_answer else _no_answer)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", _after_route, ["database", "documents", "answer"])
    g.add_conditional_edges("database", _after_database, ["documents", "answer"])
    g.add_edge("documents", "answer")
    g.add_edge("answer", END)
    return g.compile()


_GRAPH = build_graph()
_GATHER = build_graph(with_answer=False)


async def gather(question: str, deps: AgentDeps) -> AgentState:
    """Route and fetch data, without writing the answer (the caller streams it)."""
    return await _GATHER.ainvoke(
        {"question": question, "tokens": 0}, config={"configurable": {"deps": deps}}
    )


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
