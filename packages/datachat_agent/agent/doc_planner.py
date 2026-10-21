"""Decide whether a question needs line items from document tables, and how to filter them."""

import json
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

PLAN_SYSTEM = """Decide what to fetch from the uploaded documents (invoices, bills, price lists,
manuals) to answer a question. Reply with JSON only, no prose:
{"need_items": true or false, "document": string or null, "item": string or null}

- need_items: true when the answer needs numbers from document tables: totals, amounts,
  quantities, rates, prices, or comparing items across documents. false for wording, rules,
  instructions or descriptions.
- document: an invoice or document number or name the question mentions (copy it exactly),
  else null.
- item: a product or material name to filter line items by (copy it exactly), else null."""


@dataclass
class ItemPlan:
    need_items: bool = False
    document: str | None = None
    item: str | None = None
    tokens: int = 0


def _text(value) -> str | None:
    if isinstance(value, str) and value.strip() and value.strip().lower() != "null":
        return value.strip()[:200]
    return None


async def plan_items(llm, question: str) -> ItemPlan:
    try:
        response = await llm.ainvoke([SystemMessage(PLAN_SYSTEM), HumanMessage(question)])
        raw = str(response.content)
        data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
    except Exception:  # noqa: BLE001 - without a plan, answer from passages only
        return ItemPlan()
    usage = getattr(response, "usage_metadata", None) or {}
    return ItemPlan(
        need_items=bool(data.get("need_items")),
        document=_text(data.get("document")),
        item=_text(data.get("item")),
        tokens=int(usage.get("total_tokens", 0)),
    )
