"""Decide where an answer can come from: the database, the documents, both, or neither."""

from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

Route = Literal["database", "documents", "both", "none"]

ROUTER_SYSTEM = """Classify where the answer to a business question will be found.

- database: numbers, lists, totals, statuses or records stored in the company's tables
{tables}
- documents: content of uploaded PDFs such as invoices, manuals, contracts, price lists
- both: the question needs a fact from each

Reply with exactly one word: database, documents or both."""


async def choose_route(
    llm: BaseChatModel | Runnable | None,
    question: str,
    has_database: bool,
    has_documents: bool,
    table_names: list[str] | None = None,
) -> tuple[Route, int]:
    """Return (route, tokens used). The model is only asked when both sources exist."""
    if not has_database and not has_documents:
        return "none", 0
    if has_database and not has_documents:
        return "database", 0
    if has_documents and not has_database:
        return "documents", 0

    tables = f"  (tables: {', '.join(table_names[:40])})" if table_names else ""
    try:
        response = await llm.ainvoke(
            [SystemMessage(ROUTER_SYSTEM.format(tables=tables)), HumanMessage(question)]
        )
    except Exception:  # noqa: BLE001 - when unsure, look in both places
        return "both", 0
    word = str(response.content).strip().lower().strip(".")
    usage = getattr(response, "usage_metadata", None) or {}
    route: Route = word if word in ("database", "documents", "both") else "both"  # type: ignore[assignment]
    return route, int(usage.get("total_tokens", 0))
