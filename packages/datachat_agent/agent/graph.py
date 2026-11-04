"""The agent: route -> (database and/or documents) -> answer, as a LangGraph graph."""

import asyncio
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
from datachat_agent.agent.doc_planner import ItemPlan, plan_items
from datachat_agent.agent.knowledge import Knowledge, load_knowledge
from datachat_agent.agent.router import Route, choose_route
from datachat_agent.agent.sessions import Turn
from datachat_agent.config import get_settings
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
    has_documents: bool
    item_plan: ItemPlan | None


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


async def _document_names(deps: AgentDeps) -> list[str]:
    rows = await deps.session.scalars(
        select(Document.filename)
        .where(Document.tenant_id == deps.tenant_id, Document.status == "ready")
        .order_by(Document.created_at.desc())
        .limit(200)
    )
    return list(rows)


async def _query_database(state: AgentState, deps: AgentDeps, partial: bool) -> SQLAnswer:
    question = state["question"]
    previous = deps.history[-1].question if deps.history else ""
    base_text = f"{previous}\n{question}" if previous else question

    vector = await run_in_threadpool(deps.embedder.embed_query, base_text)
    knowledge: Knowledge = await load_knowledge(deps.session, deps.connection.id, question, vector)
    # Business terms named in the question point retrieval at the right tables.
    retrieval_text = "\n".join([base_text, *knowledge.matched_definitions])

    return await answer_with_sql(
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
        partial=partial,
        masker=deps.masker,
    )


def _sql_update(state: AgentState, result: SQLAnswer) -> AgentState:
    return {
        "sql": result,
        "tokens": state.get("tokens", 0) + result.tokens,
        "llm_unavailable": result.llm_unavailable,
    }


async def route_node(state: AgentState, config: RunnableConfig) -> AgentState:
    """Choose where to look. With PARALLEL_STEPS, also start the work the route will need.

    The routing call, the document planning call and the database query do not depend on each
    other, so they run at the same time: the answer comes about one model call sooner. Only
    the database work uses the session; the two model calls do not touch it.
    """
    deps = _deps(config)
    question = state["question"]
    has_db = deps.connection is not None
    has_docs = await _has_documents(deps)
    tables = await _table_names(deps) if has_db and has_docs else None
    names = await _document_names(deps) if has_db and has_docs else None
    routing = choose_route(deps.llm, question, has_db, has_docs, tables, names)

    if not (get_settings().parallel_steps and has_db and has_docs):
        route, tokens = await routing
        return {
            "route": route,
            "has_documents": has_docs,
            "tokens": state.get("tokens", 0) + tokens,
        }

    # The SQL is drafted before the route is known, so it may only cover part of a question
    # that also needs the documents.
    (route, tokens), plan, sql = await asyncio.gather(
        routing, plan_items(deps.llm, question), _query_database(state, deps, partial=True)
    )
    update: AgentState = {
        "route": route,
        "has_documents": has_docs,
        "item_plan": plan,
        "tokens": state.get("tokens", 0) + tokens + plan.tokens + sql.tokens,
    }
    if route in ("database", "both"):
        update["sql"] = sql
        update["llm_unavailable"] = sql.llm_unavailable
    return update


def _found_nothing(result: SQLAnswer | None) -> bool:
    if result is None or result.llm_unavailable:
        return False
    if result.cannot_answer or result.error:
        return True
    return result.result is not None and not result.result.rows


async def database_node(state: AgentState, config: RunnableConfig) -> AgentState:
    deps = _deps(config)
    update: AgentState = {}
    if state.get("sql") is None:
        result = await _query_database(state, deps, partial=state.get("route") == "both")
        update = _sql_update(state, result)
    # Nothing in the tables: the answer may still be in a document (a purchase order that was
    # only uploaded, a supplier's own invoice number), so read those before saying so.
    if (
        state.get("route") == "database"
        and state.get("has_documents")
        and _found_nothing(update.get("sql", state.get("sql")))
    ):
        update["route"] = "both"
    return update


async def documents_node(state: AgentState, config: RunnableConfig) -> AgentState:
    """Hybrid passage search, plus line items when the question needs table numbers."""
    deps = _deps(config)
    question = state["question"]
    previous = deps.history[-1].question if deps.history else ""
    search_text = f"{previous}\n{question}" if previous else question
    vector = await run_in_threadpool(deps.embedder.embed_query, search_text)
    passages = await search_passages(deps.session, deps.tenant_id, search_text, vector)

    plan = state.get("item_plan") or await plan_items(deps.llm, question)
    already_counted = state.get("item_plan") is not None
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
        "tokens": state.get("tokens", 0) + (0 if already_counted else plan.tokens),
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
