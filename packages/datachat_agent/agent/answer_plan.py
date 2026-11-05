"""The last step of the agent, split so it can run whole (POST /v1/ask) or streamed.

plan_answer() decides what the answer needs: a fixed reply, or one model call with these
messages. finalize() turns the model's text into the answer, chart and citations; on_error()
says what to show when the model fails. Both endpoints share this, so they always agree.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from datachat_agent.agent.answer import (
    I_DONT_KNOW,
    build_answer_messages,
    chart_hint,
    plain_summary,
)
from datachat_agent.agent.cited_answer import build_cited_messages, cited_refs, number_sources
from datachat_agent.core.llm import describe_llm_error
from datachat_agent.core.pii import PiiMasker

NO_SOURCE = "No data source is connected yet, so I can't answer that."

Update = dict[str, Any]


@dataclass
class AnswerPlan:
    fixed: Update | None = None
    messages: list | None = None
    finalize: Callable[[str, int], Update] | None = None
    on_error: Callable[[Exception], Update] | None = None


def _mask(masker: PiiMasker | None, value):
    return masker.mask(value) if masker else value


def table_plan(
    question: str,
    table: dict[str, Any],
    history: list[tuple[str, str]],
    masker: PiiMasker | None = None,
) -> AnswerPlan:
    cols, rows = table["columns"], table["rows"]
    count, truncated = table["row_count"], table["truncated"]
    messages = build_answer_messages(
        question,
        cols,
        _mask(masker, rows),
        count,
        truncated,
        [(q, _mask(masker, a)) for q, a in history],
        table.get("sql"),
    )

    def finalize(text: str, tokens: int) -> Update:
        return {
            "answer": text.strip() or plain_summary(rows, count, truncated),
            "chart": chart_hint(cols, rows),
            "tokens_used": tokens,
            "error": None,
        }

    def on_error(e: Exception) -> Update:  # the data is already there; never lose it
        return {
            "answer": plain_summary(rows, count, truncated) + f" ({describe_llm_error(e)})",
            "chart": chart_hint(cols, rows),
            # The data is shown, but the written answer is missing: say why, so a caller
            # knows to ask again.
            "error": describe_llm_error(e),
            "fallback": True,
        }

    return AnswerPlan(messages=messages, finalize=finalize, on_error=on_error)


def cited_plan(
    question: str,
    passages: list,
    items: list,
    table: dict[str, Any] | None,
    history: list[tuple[str, str]],
    masker: PiiMasker | None = None,
) -> AnswerPlan:
    sources, passage_payload, item_payload = number_sources(passages, items)
    model_table = dict(table, rows=_mask(masker, table["rows"])) if table else None
    messages = build_cited_messages(
        question,
        _mask(masker, passage_payload),
        _mask(masker, item_payload),
        model_table,
        [(q, _mask(masker, a)) for q, a in history],
    )

    def finalize(text: str, tokens: int) -> Update:
        text = text.strip()
        if text.lower().startswith("i don't know"):
            return {
                "answer": text,
                "citations": [],
                "chart": None,
                "tokens_used": tokens,
                "error": None,
            }
        used = cited_refs(text, sources)
        if not used and (passages or items) and table is None:
            used = sources[:1]  # an answer from documents always points somewhere
        return {
            "answer": text,
            "citations": [s.as_citation() for s in used],
            "chart": chart_hint(table["columns"], table["rows"]) if table else None,
            "tokens_used": tokens,
            "error": None,
        }

    def on_error(e: Exception) -> Update:  # still show where the answer can be found
        top = sources[:3]
        names = "; ".join(f"{s.filename} page {s.page}" for s in top)
        text = (
            f"The most relevant places are: {names}. ({describe_llm_error(e)})"
            if top
            else describe_llm_error(e)
        )
        return {
            "answer": text,
            "citations": [s.as_citation() for s in top],
            "chart": None,
            "error": describe_llm_error(e),
            "fallback": True,
        }

    return AnswerPlan(messages=messages, finalize=finalize, on_error=on_error)


def plan_answer(
    state: dict[str, Any], history: list[tuple[str, str]], masker: PiiMasker | None = None
) -> AnswerPlan:
    sql = state.get("sql")
    passages = state.get("documents") or []
    items = state.get("items") or []

    if state["route"] == "none":
        return AnswerPlan(fixed={"answer": NO_SOURCE, "chart": None, "error": None})
    if state.get("llm_unavailable"):
        error = sql.error if sql else "The language model is unavailable."
        return AnswerPlan(fixed={"answer": error, "chart": None, "error": error})

    table = None
    if sql is not None and sql.result is not None:
        r = sql.result
        table = {
            "columns": r.columns,
            "rows": r.rows,
            "row_count": r.row_count,
            "truncated": r.truncated,
            "sql": sql.sql,
        }

    if passages or items:
        return cited_plan(state["question"], passages, items, table, history, masker)
    if table is not None:
        return table_plan(state["question"], table, history, masker)
    if sql is not None and sql.error and not sql.cannot_answer:
        error = sql.error
        return AnswerPlan(
            fixed={"answer": "I couldn't get that data: " + error, "chart": None, "error": error}
        )
    return AnswerPlan(fixed={"answer": I_DONT_KNOW, "chart": None, "error": None})


def _content(response) -> str:
    return response.content if isinstance(response.content, str) else str(response.content)


async def run_plan(plan: AnswerPlan, llm, masker: PiiMasker | None = None) -> Update:
    """Execute a plan with one (non-streamed) model call."""
    if plan.fixed is not None:
        return dict(plan.fixed)
    try:
        response = await llm.ainvoke(plan.messages)
    except Exception as e:  # noqa: BLE001
        return plan.on_error(e)
    text = _content(response)
    if masker:
        text = masker.unmask(text)
    usage = getattr(response, "usage_metadata", None) or {}
    return plan.finalize(text, int(usage.get("total_tokens", 0)))
