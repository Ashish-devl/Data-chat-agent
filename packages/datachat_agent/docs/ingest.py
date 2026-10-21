"""Store an uploaded PDF, then turn it into searchable passages and line items."""

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from datachat_agent.core.embeddings import Embedder
from datachat_agent.docs.chunker import chunk_pages
from datachat_agent.docs.extract import NoTextLayerError, extract_pdf
from datachat_agent.docs.items import extract_items
from datachat_agent.server.models import Document, DocumentChunk, DocumentItem

PDF_MAGIC = b"%PDF-"


class UploadRejected(ValueError):
    pass


@dataclass
class Stored:
    document: Document
    created: bool  # False when the same file was already uploaded


def check_pdf(data: bytes, max_bytes: int) -> None:
    if len(data) > max_bytes:
        raise UploadRejected(f"The file is larger than {max_bytes // 1_000_000} MB.")
    if not data.startswith(PDF_MAGIC):
        raise UploadRejected("Only PDF files are accepted.")


async def store_document(
    session: AsyncSession, tenant_id: uuid.UUID, filename: str, data: bytes, max_bytes: int
) -> Stored:
    check_pdf(data, max_bytes)
    digest = hashlib.sha256(data).hexdigest()
    existing = await session.scalar(
        select(Document).where(Document.tenant_id == tenant_id, Document.sha256 == digest)
    )
    if existing is not None:
        return Stored(existing, created=False)
    doc = Document(
        tenant_id=tenant_id,
        filename=filename[:500] or "document.pdf",
        mime="application/pdf",
        size_bytes=len(data),
        sha256=digest,
        file_bytes=data,
        status="queued",
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return Stored(doc, created=True)


def embedding_text(filename: str, heading: str | None, text: str) -> str:
    title = f"{filename} — {heading}" if heading else filename
    return f"{title}\n{text}"


async def ingest_document(
    session: AsyncSession, document_id: uuid.UUID, embedder: Embedder, llm=None
) -> Document:
    """Extract, chunk, embed and save. Safe to run again: old passages and items are replaced."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise ValueError(f"Document {document_id} not found")
    doc.status, doc.error = "processing", None
    await session.commit()

    data = (
        await session.execute(select(Document.file_bytes).where(Document.id == document_id))
    ).scalar_one()
    try:
        extracted = await run_in_threadpool(extract_pdf, data)
    except NoTextLayerError as e:
        return await _fail(session, doc, str(e))
    except Exception as e:  # noqa: BLE001 - a broken PDF must not crash the worker
        return await _fail(session, doc, f"Could not read the PDF: {str(e)[:200]}")

    try:
        items = await extract_items(extracted.tables, llm)
        chunks = chunk_pages(extracted.pages)
        vectors = await run_in_threadpool(
            embedder.embed_documents,
            [embedding_text(doc.filename, c.heading, c.text) for c in chunks],
        )
    except Exception as e:  # noqa: BLE001
        return await _fail(session, doc, f"Processing failed: {str(e)[:200]}")

    await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    await session.execute(delete(DocumentItem).where(DocumentItem.document_id == doc.id))
    session.add_all(
        DocumentChunk(
            document_id=doc.id,
            page=c.page,
            chunk_index=c.index,
            heading=c.heading,
            text=c.text,
            embedding=v,
        )
        for c, v in zip(chunks, vectors, strict=True)
    )
    session.add_all(
        DocumentItem(
            document_id=doc.id,
            page=i.page,
            row_index=i.row_index,
            item=i.item,
            qty=i.qty,
            unit=i.unit,
            rate=i.rate,
            amount=i.amount,
            raw=i.raw,
        )
        for i in items
    )
    doc.pages, doc.doc_date, doc.status = extracted.page_count, extracted.doc_date, "ready"
    await session.commit()
    return doc


async def _fail(session: AsyncSession, doc: Document, reason: str) -> Document:
    doc.status, doc.error = "failed", reason
    await session.commit()
    return doc
