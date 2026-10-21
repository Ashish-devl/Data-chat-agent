"""Hybrid passage search (vector + keyword, merged with reciprocal rank fusion) and item lookup."""

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Text, and_, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.server.models import Document, DocumentChunk, DocumentItem

CANDIDATES = 20
TOP_K = 6
RRF_K = 60
MAX_ITEMS = 200


@dataclass
class Passage:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page: int
    heading: str | None
    text: str
    score: float


@dataclass
class ItemRow:
    document_id: uuid.UUID
    filename: str
    doc_date: dt.date | None
    page: int
    item: str | None
    qty: Decimal | None
    unit: str | None
    rate: Decimal | None
    amount: Decimal | None


def _ready_docs(tenant_id: uuid.UUID):
    return and_(Document.tenant_id == tenant_id, Document.status == "ready")


def _or_query(text: str):
    """Match any of the words (ranked by how many), not all of them."""
    plain = func.plainto_tsquery("english", text)
    return func.to_tsquery("english", func.replace(cast(plain, Text), "&", "|"))


async def search_passages(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    query: str,
    query_vector: list[float],
    top_k: int = TOP_K,
) -> list[Passage]:
    base = (
        select(DocumentChunk.id)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(_ready_docs(tenant_id))
    )
    by_vector = list(
        await session.scalars(
            base.where(DocumentChunk.embedding.is_not(None))
            .order_by(DocumentChunk.embedding.cosine_distance(query_vector))
            .limit(CANDIDATES)
        )
    )
    tsq = _or_query(query)
    by_keyword = list(
        await session.scalars(
            base.where(DocumentChunk.tsv.op("@@")(tsq))
            .order_by(func.ts_rank_cd(DocumentChunk.tsv, tsq).desc())
            .limit(CANDIDATES)
        )
    )

    scores: dict[uuid.UUID, float] = {}
    for ranking in (by_vector, by_keyword):
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    best = sorted(scores, key=scores.get, reverse=True)[:top_k]
    if not best:
        return []

    rows = await session.execute(
        select(DocumentChunk, Document.filename)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.id.in_(best))
    )
    found = {
        c.id: Passage(c.id, c.document_id, filename, c.page, c.heading, c.text, scores[c.id])
        for c, filename in rows
    }
    return [found[i] for i in best if i in found]


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def find_items(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    document: str | None = None,
    item: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    limit: int = MAX_ITEMS,
) -> list[ItemRow]:
    """Line items, filtered by document (file name or any text in it), item name and date."""
    stmt = (
        select(DocumentItem, Document.filename, Document.doc_date)
        .join(Document, Document.id == DocumentItem.document_id)
        .where(_ready_docs(tenant_id))
    )
    if document:
        pattern = _like(document.strip())
        mentions = exists().where(
            DocumentChunk.document_id == Document.id,
            DocumentChunk.text.ilike(pattern, escape="\\"),
        )
        stmt = stmt.where(or_(Document.filename.ilike(pattern, escape="\\"), mentions))
    if item:
        stmt = stmt.where(DocumentItem.item.ilike(_like(item.strip()), escape="\\"))
    if date_from:
        stmt = stmt.where(Document.doc_date >= date_from)
    if date_to:
        stmt = stmt.where(Document.doc_date <= date_to)
    stmt = stmt.order_by(
        Document.doc_date, Document.filename, DocumentItem.page, DocumentItem.row_index
    ).limit(limit)
    return [
        ItemRow(i.document_id, filename, doc_date, i.page, i.item, i.qty, i.unit, i.rate, i.amount)
        for i, filename, doc_date in await session.execute(stmt)
    ]
