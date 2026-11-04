"""Decide where an answer can come from: the database, the documents, both, or neither."""

import re
from pathlib import PurePath
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

Route = Literal["database", "documents", "both", "none"]

ROUTER_SYSTEM = """Classify where the answer to a business question will be found.

- database: numbers, lists, totals, statuses or records stored in the company's tables
{tables}
- documents: content of uploaded PDFs such as invoices, manuals, contracts, price lists
{documents}
- both: the question needs a fact from each

A question about one specific invoice, challan, purchase order, report or other paper, named
by its number, is about documents.

Reply with exactly one word: database, documents or both."""

# A reference such as ATC-5502, DC-3307, PO/26-27/4101 or TR-2026-552: letters and digits
# (both), at least four of them once separators are dropped.
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9/._-]{2,}[A-Za-z0-9]")


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def names_a_document(question: str, document_names: list[str]) -> bool:
    """True when the question quotes a reference number that is part of a file's name."""
    stems = [_squash(PurePath(n).stem) for n in document_names]
    for token in _REFERENCE.findall(question):
        if not (re.search(r"\d", token) and re.search(r"[A-Za-z]", token)):
            continue  # a bare year or quantity is not a reference
        ref = _squash(token)
        if len(ref) >= 4 and any(ref in stem for stem in stems):
            return True
    return False


async def choose_route(
    llm: BaseChatModel | Runnable | None,
    question: str,
    has_database: bool,
    has_documents: bool,
    table_names: list[str] | None = None,
    document_names: list[str] | None = None,
) -> tuple[Route, int]:
    """Return (route, tokens used). The model is only asked when both sources exist."""
    if not has_database and not has_documents:
        return "none", 0
    if has_database and not has_documents:
        return "database", 0
    if has_documents and not has_database:
        return "documents", 0

    tables = f"  (tables: {', '.join(table_names[:40])})" if table_names else ""
    documents = f"  (files: {', '.join(document_names[:30])})" if document_names else ""
    try:
        response = await llm.ainvoke(
            [
                SystemMessage(ROUTER_SYSTEM.format(tables=tables, documents=documents)),
                HumanMessage(question),
            ]
        )
    except Exception:  # noqa: BLE001 - when unsure, look in both places
        return "both", 0
    word = str(response.content).strip().lower().strip(".")
    usage = getattr(response, "usage_metadata", None) or {}
    route: Route = word if word in ("database", "documents", "both") else "both"  # type: ignore[assignment]
    # A document named by its number is worth reading even if the model chose the database.
    if route == "database" and names_a_document(question, document_names or []):
        route = "both"
    return route, int(usage.get("total_tokens", 0))
