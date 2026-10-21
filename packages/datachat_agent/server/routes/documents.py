import datetime as dt
import uuid
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.config import get_settings
from datachat_agent.docs.ingest import UploadRejected, store_document
from datachat_agent.security import Scope
from datachat_agent.server.auth import Principal, require_scope
from datachat_agent.server.db import get_session
from datachat_agent.server.jobs import enqueue_ingest
from datachat_agent.server.models import Document, DocumentChunk, DocumentItem

router = APIRouter(prefix="/documents", tags=["documents"])
docs_scope = require_scope(Scope.DOCS)


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    size_bytes: int
    sha256: str
    pages: int | None
    doc_date: dt.date | None
    status: str
    error: str | None
    chunks: int = 0
    items: int = 0
    created_at: datetime


class ItemOut(BaseModel):
    id: uuid.UUID
    page: int
    row_index: int
    item: str | None
    qty: float | None
    unit: str | None
    rate: float | None
    amount: float | None


def _num(v) -> float | None:
    return float(v) if v is not None else None


async def _counts(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
    if not ids:
        return {}
    chunks = dict(
        (
            await session.execute(
                select(DocumentChunk.document_id, func.count())
                .where(DocumentChunk.document_id.in_(ids))
                .group_by(DocumentChunk.document_id)
            )
        ).all()
    )
    items = dict(
        (
            await session.execute(
                select(DocumentItem.document_id, func.count())
                .where(DocumentItem.document_id.in_(ids))
                .group_by(DocumentItem.document_id)
            )
        ).all()
    )
    return {i: (chunks.get(i, 0), items.get(i, 0)) for i in ids}


def _out(d: Document, counts: tuple[int, int] = (0, 0)) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        filename=d.filename,
        size_bytes=d.size_bytes,
        sha256=d.sha256,
        pages=d.pages,
        doc_date=d.doc_date,
        status=d.status,
        error=d.error,
        chunks=counts[0],
        items=counts[1],
        created_at=d.created_at,
    )


async def _owned(document_id: uuid.UUID, principal: Principal, session: AsyncSession) -> Document:
    doc = await session.scalar(
        select(Document).where(
            Document.id == document_id, Document.tenant_id == principal.tenant_id
        )
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DocumentOut)
async def upload_document(
    file: UploadFile = File(..., description="A PDF with a text layer, up to 20 MB"),
    principal: Principal = Depends(docs_scope),
    session: AsyncSession = Depends(get_session),
):
    """Upload a PDF. The same file uploaded twice returns the existing document (200)."""
    max_bytes = get_settings().max_pdf_mb * 1_000_000
    data = await file.read(max_bytes + 1)
    try:
        stored = await store_document(
            session, principal.tenant_id, file.filename or "document.pdf", data, max_bytes
        )
    except UploadRejected as e:
        code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if "larger" in str(e)
            else status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        )
        raise HTTPException(code, str(e)) from None
    doc = stored.document
    if not stored.created:
        counts = (await _counts(session, [doc.id]))[doc.id]
        return JSONResponse(status_code=200, content=_out(doc, counts).model_dump(mode="json"))
    await enqueue_ingest(doc.id)
    await session.refresh(doc)
    counts = (await _counts(session, [doc.id]))[doc.id]
    return _out(doc, counts)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    principal: Principal = Depends(docs_scope),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentOut]:
    docs = list(
        await session.scalars(
            select(Document)
            .where(Document.tenant_id == principal.tenant_id)
            .order_by(Document.created_at.desc())
        )
    )
    counts = await _counts(session, [d.id for d in docs])
    return [_out(d, counts[d.id]) for d in docs]


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(docs_scope),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    doc = await _owned(document_id, principal, session)
    return _out(doc, (await _counts(session, [doc.id]))[doc.id])


@router.get("/{document_id}/file")
async def download_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(docs_scope),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """The original PDF, byte for byte."""
    doc = await _owned(document_id, principal, session)
    data = (
        await session.execute(select(Document.file_bytes).where(Document.id == doc.id))
    ).scalar_one()
    name = quote(doc.filename)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"},
    )


@router.get("/{document_id}/items", response_model=list[ItemOut])
async def document_items(
    document_id: uuid.UUID,
    principal: Principal = Depends(docs_scope),
    session: AsyncSession = Depends(get_session),
) -> list[ItemOut]:
    doc = await _owned(document_id, principal, session)
    rows = await session.scalars(
        select(DocumentItem)
        .where(DocumentItem.document_id == doc.id)
        .order_by(DocumentItem.page, DocumentItem.row_index)
    )
    return [
        ItemOut(
            id=i.id,
            page=i.page,
            row_index=i.row_index,
            item=i.item,
            qty=_num(i.qty),
            unit=i.unit,
            rate=_num(i.rate),
            amount=_num(i.amount),
        )
        for i in rows
    ]


@router.post("/{document_id}/reprocess", response_model=DocumentOut, status_code=202)
async def reprocess_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(docs_scope),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    """Run extraction again, e.g. after a failure or after configuring a model."""
    doc = await _owned(document_id, principal, session)
    doc.status, doc.error = "queued", None
    await session.commit()
    await enqueue_ingest(doc.id)
    await session.refresh(doc)
    return _out(doc, (await _counts(session, [doc.id]))[doc.id])


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(docs_scope),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Removes the PDF, its passages and its line items."""
    doc = await _owned(document_id, principal, session)
    await session.delete(doc)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
