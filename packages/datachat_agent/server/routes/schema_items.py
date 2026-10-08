import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from datachat_agent.core.embeddings import get_embedder
from datachat_agent.security import Scope
from datachat_agent.server.auth import Principal, require_scope
from datachat_agent.server.db import get_session
from datachat_agent.server.models import Connection, SchemaItem
from datachat_agent.sql.schema_store import embedding_text

router = APIRouter(prefix="/schema-items", tags=["semantic layer"])


class SchemaItemUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    hidden: bool | None = None


class SchemaItemOut(BaseModel):
    id: uuid.UUID
    schema_name: str
    table_name: str
    column_name: str | None
    description: str | None
    hidden: bool


@router.patch("/{item_id}", response_model=SchemaItemOut)
async def update_schema_item(
    item_id: uuid.UUID,
    body: SchemaItemUpdate,
    principal: Principal = Depends(require_scope(Scope.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> SchemaItemOut:
    item = await session.scalar(
        select(SchemaItem)
        .join(Connection, Connection.id == SchemaItem.connection_id)
        .where(SchemaItem.id == item_id, Connection.tenant_id == principal.tenant_id)
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schema item not found")

    if "description" in body.model_fields_set:
        item.description = (body.description or "").strip() or None
        vector = await run_in_threadpool(get_embedder().embed_documents, [embedding_text(item)])
        item.embedding = vector[0]
    if body.hidden is not None:
        item.hidden = body.hidden
        if body.hidden:
            # Never keep sample values of anything hidden; for a table, of all its columns.
            item.sample_values = []
            if item.column_name is None:
                for col in await session.scalars(
                    select(SchemaItem).where(
                        SchemaItem.connection_id == item.connection_id,
                        SchemaItem.schema_name == item.schema_name,
                        SchemaItem.table_name == item.table_name,
                    )
                ):
                    col.sample_values = []
    await session.commit()
    return SchemaItemOut(
        id=item.id,
        schema_name=item.schema_name,
        table_name=item.table_name,
        column_name=item.column_name,
        description=item.description,
        hidden=item.hidden,
    )
