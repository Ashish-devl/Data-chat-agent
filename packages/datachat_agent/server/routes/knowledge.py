"""Business terms and worked SQL examples: the parts of the semantic layer admins write."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from datachat_agent.agent.knowledge import term_text
from datachat_agent.config import get_settings
from datachat_agent.core.embeddings import get_embedder
from datachat_agent.security import Scope
from datachat_agent.server.auth import Principal, require_scope
from datachat_agent.server.db import get_session
from datachat_agent.server.models import GlossaryTerm, SqlExample
from datachat_agent.server.routes.connections import get_owned_connection
from datachat_agent.sql.schema_store import build_policy
from datachat_agent.sql.validator import SQLValidationError, validate_sql

router = APIRouter(tags=["semantic layer"])
admin = require_scope(Scope.ADMIN)


class TermIn(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    definition: str = Field(min_length=1, max_length=2000)


class TermUpdate(BaseModel):
    term: str | None = Field(default=None, min_length=1, max_length=200)
    definition: str | None = Field(default=None, min_length=1, max_length=2000)


class TermOut(BaseModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    term: str
    definition: str
    created_at: datetime


class ExampleIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(min_length=1, max_length=20000)


class ExampleOut(BaseModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    question: str
    sql: str
    created_at: datetime


def _term_out(t: GlossaryTerm) -> TermOut:
    return TermOut(
        id=t.id,
        connection_id=t.connection_id,
        term=t.term,
        definition=t.definition,
        created_at=t.created_at,
    )


def _example_out(e: SqlExample) -> ExampleOut:
    return ExampleOut(
        id=e.id,
        connection_id=e.connection_id,
        question=e.question,
        sql=e.sql,
        created_at=e.created_at,
    )


async def _embed(text: str) -> list[float]:
    return (await run_in_threadpool(get_embedder().embed_documents, [text]))[0]


@router.get("/connections/{connection_id}/terms", response_model=list[TermOut])
async def list_terms(
    connection_id: uuid.UUID,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> list[TermOut]:
    conn = await get_owned_connection(connection_id, principal, session)
    rows = await session.scalars(
        select(GlossaryTerm)
        .where(GlossaryTerm.connection_id == conn.id)
        .order_by(GlossaryTerm.term)
    )
    return [_term_out(t) for t in rows]


@router.post(
    "/connections/{connection_id}/terms",
    status_code=status.HTTP_201_CREATED,
    response_model=TermOut,
)
async def create_term(
    connection_id: uuid.UUID,
    body: TermIn,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> TermOut:
    conn = await get_owned_connection(connection_id, principal, session)
    term = GlossaryTerm(
        tenant_id=principal.tenant_id,
        connection_id=conn.id,
        term=body.term.strip(),
        definition=body.definition.strip(),
        embedding=await _embed(term_text(body.term.strip(), body.definition.strip())),
    )
    session.add(term)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That term already exists") from None
    await session.refresh(term)
    return _term_out(term)


async def _owned_term(term_id: uuid.UUID, principal: Principal, session: AsyncSession):
    term = await session.scalar(
        select(GlossaryTerm).where(
            GlossaryTerm.id == term_id, GlossaryTerm.tenant_id == principal.tenant_id
        )
    )
    if term is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Term not found")
    return term


@router.patch("/terms/{term_id}", response_model=TermOut)
async def update_term(
    term_id: uuid.UUID,
    body: TermUpdate,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> TermOut:
    term = await _owned_term(term_id, principal, session)
    if body.term is not None:
        term.term = body.term.strip()
    if body.definition is not None:
        term.definition = body.definition.strip()
    term.embedding = await _embed(term_text(term.term, term.definition))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That term already exists") from None
    await session.refresh(term)
    return _term_out(term)


@router.delete("/terms/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_term(
    term_id: uuid.UUID,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await session.delete(await _owned_term(term_id, principal, session))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/connections/{connection_id}/examples", response_model=list[ExampleOut])
async def list_examples(
    connection_id: uuid.UUID,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> list[ExampleOut]:
    conn = await get_owned_connection(connection_id, principal, session)
    rows = await session.scalars(
        select(SqlExample)
        .where(SqlExample.connection_id == conn.id)
        .order_by(SqlExample.created_at)
    )
    return [_example_out(e) for e in rows]


@router.post(
    "/connections/{connection_id}/examples",
    status_code=status.HTTP_201_CREATED,
    response_model=ExampleOut,
)
async def create_example(
    connection_id: uuid.UUID,
    body: ExampleIn,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> ExampleOut:
    conn = await get_owned_connection(connection_id, principal, session)
    if conn.last_scanned_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Scan the connection before adding examples")
    # Examples teach the model, so they must pass the same rules as its own queries.
    # Row filters are left out here: they are applied per user when the query actually runs.
    policy = await build_policy(session, conn)
    policy.row_filters = {}
    try:
        validated = validate_sql(body.sql, conn.dialect, policy, get_settings().sql_max_rows)
    except SQLValidationError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Example SQL refused: {e}"
        ) from None
    example = SqlExample(
        tenant_id=principal.tenant_id,
        connection_id=conn.id,
        question=body.question.strip(),
        sql=validated.sql,
        embedding=await _embed(body.question.strip()),
    )
    session.add(example)
    await session.commit()
    await session.refresh(example)
    return _example_out(example)


@router.delete("/examples/{example_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_example(
    example_id: uuid.UUID,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    example = await session.scalar(
        select(SqlExample).where(
            SqlExample.id == example_id, SqlExample.tenant_id == principal.tenant_id
        )
    )
    if example is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Example not found")
    await session.delete(example)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
