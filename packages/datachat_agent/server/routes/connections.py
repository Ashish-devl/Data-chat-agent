import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from datachat_agent.core.embeddings import get_embedder
from datachat_agent.security import Scope, decrypt, encrypt
from datachat_agent.server.auth import Principal, require_scope
from datachat_agent.server.db import get_session
from datachat_agent.server.models import Connection, SchemaItem
from datachat_agent.sql.connector import (
    ConnectionConfigError,
    check_read_only,
    dispose_engine,
    get_engine,
    normalize_dsn,
)
from datachat_agent.sql.schema_scanner import scan_schema
from datachat_agent.sql.schema_store import save_scan

router = APIRouter(prefix="/connections", tags=["connections"])
admin = require_scope(Scope.ADMIN)


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    dsn: str = Field(min_length=1, description="postgresql://user:pass@host/db or mysql://...")
    schema_allowlist: list[str] = Field(default_factory=list, max_length=50)
    row_filters: dict[str, str] = Field(
        default_factory=dict,
        description='Per-table SQL conditions, e.g. {"orders": "company_id = :user_company"}',
    )


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    dsn: str | None = Field(default=None, min_length=1)
    schema_allowlist: list[str] | None = Field(default=None, max_length=50)
    row_filters: dict[str, str] | None = None


class ConnectionOut(BaseModel):
    id: uuid.UUID
    name: str
    dialect: str
    target: str  # host/database with the password removed
    schema_allowlist: list[str]
    row_filters: dict[str, Any]
    status: str
    status_detail: str | None
    last_scanned_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, c: Connection) -> "ConnectionOut":
        url = make_url(decrypt(c.dsn_encrypted))
        port = f":{url.port}" if url.port else ""
        target = f"{url.username or ''}@{url.host or ''}{port}/{url.database}"
        return cls(
            id=c.id,
            name=c.name,
            dialect=c.dialect,
            target=target,
            schema_allowlist=list(c.schema_allowlist or []),
            row_filters=dict(c.row_filters or {}),
            status=c.status,
            status_detail=c.status_detail,
            last_scanned_at=c.last_scanned_at,
            created_at=c.created_at,
        )


class ScanResult(BaseModel):
    connection: ConnectionOut
    tables: int
    columns: int
    removed: int


class SchemaColumnOut(BaseModel):
    id: uuid.UUID
    name: str
    data_type: str | None
    is_primary_key: bool
    foreign_key: str | None
    description: str | None
    sample_values: list[Any]
    hidden: bool


class SchemaTableOut(BaseModel):
    id: uuid.UUID
    schema_name: str
    name: str
    kind: str | None
    row_count: int | None
    description: str | None
    hidden: bool
    columns: list[SchemaColumnOut]


async def get_owned_connection(
    connection_id: uuid.UUID, principal: Principal, session: AsyncSession
) -> Connection:
    conn = await session.scalar(
        select(Connection).where(
            Connection.id == connection_id, Connection.tenant_id == principal.tenant_id
        )
    )
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connection not found")
    return conn


def _parse_dsn(dsn: str) -> str:
    try:
        dialect, _ = normalize_dsn(dsn)
    except ConnectionConfigError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    return dialect


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ConnectionOut)
async def create_connection(
    body: ConnectionCreate,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> ConnectionOut:
    dialect = _parse_dsn(body.dsn)
    conn = Connection(
        tenant_id=principal.tenant_id,
        name=body.name,
        dialect=dialect,
        dsn_encrypted=encrypt(body.dsn.strip()),
        schema_allowlist=body.schema_allowlist,
        row_filters=body.row_filters,
        status="new",
    )
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return ConnectionOut.of(conn)


@router.get("", response_model=list[ConnectionOut])
async def list_connections(
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> list[ConnectionOut]:
    rows = await session.scalars(
        select(Connection)
        .where(Connection.tenant_id == principal.tenant_id)
        .order_by(Connection.created_at)
    )
    return [ConnectionOut.of(c) for c in rows]


@router.get("/{connection_id}", response_model=ConnectionOut)
async def get_connection(
    connection_id: uuid.UUID,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> ConnectionOut:
    return ConnectionOut.of(await get_owned_connection(connection_id, principal, session))


@router.patch("/{connection_id}", response_model=ConnectionOut)
async def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionUpdate,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> ConnectionOut:
    conn = await get_owned_connection(connection_id, principal, session)
    if body.dsn is not None:
        dialect = _parse_dsn(body.dsn)
        dispose_engine(decrypt(conn.dsn_encrypted))
        conn.dsn_encrypted, conn.dialect = encrypt(body.dsn.strip()), dialect
        conn.status, conn.status_detail = "new", "Connection string changed; test it again."
    if body.name is not None:
        conn.name = body.name
    if body.schema_allowlist is not None:
        conn.schema_allowlist = body.schema_allowlist
    if body.row_filters is not None:
        conn.row_filters = body.row_filters
    await session.commit()
    await session.refresh(conn)
    return ConnectionOut.of(conn)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    conn = await get_owned_connection(connection_id, principal, session)
    dispose_engine(decrypt(conn.dsn_encrypted))
    await session.delete(conn)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _test(conn: Connection, session: AsyncSession) -> bool:
    engine = get_engine(decrypt(conn.dsn_encrypted))
    result = await run_in_threadpool(check_read_only, engine, conn.dialect)
    conn.status = "ok" if result.ok else "rejected"
    conn.status_detail = result.detail
    await session.commit()
    await session.refresh(conn)
    return result.ok


@router.post("/{connection_id}/test", response_model=ConnectionOut)
async def test_connection(
    connection_id: uuid.UUID,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> ConnectionOut:
    conn = await get_owned_connection(connection_id, principal, session)
    await _test(conn, session)
    return ConnectionOut.of(conn)


@router.post("/{connection_id}/scan", response_model=ScanResult)
async def scan_connection(
    connection_id: uuid.UUID,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> ScanResult:
    conn = await get_owned_connection(connection_id, principal, session)
    # Re-check every time: grants can change after the first test.
    if not await _test(conn, session):
        raise HTTPException(status.HTTP_409_CONFLICT, conn.status_detail or "Connection rejected")
    hidden = {
        (i.schema_name, i.table_name, i.column_name)
        for i in await session.scalars(
            select(SchemaItem).where(SchemaItem.connection_id == conn.id, SchemaItem.hidden)
        )
    }
    engine = get_engine(decrypt(conn.dsn_encrypted))
    try:
        tables = await run_in_threadpool(
            scan_schema, engine, conn.dialect, list(conn.schema_allowlist or []) or None, hidden
        )
    except Exception as e:  # noqa: BLE001 - report any driver error to the admin
        conn.status, conn.status_detail = "error", f"Scan failed: {str(e).splitlines()[0]}"
        await session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, conn.status_detail) from None
    counts = await save_scan(session, conn, tables, get_embedder())
    await session.refresh(conn)
    return ScanResult(connection=ConnectionOut.of(conn), **counts)


@router.get("/{connection_id}/schema", response_model=list[SchemaTableOut])
async def get_schema(
    connection_id: uuid.UUID,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> list[SchemaTableOut]:
    conn = await get_owned_connection(connection_id, principal, session)
    items = list(
        await session.scalars(
            select(SchemaItem)
            .where(SchemaItem.connection_id == conn.id)
            .order_by(SchemaItem.schema_name, SchemaItem.table_name, SchemaItem.column_name)
        )
    )
    tables: dict[tuple[str, str], SchemaTableOut] = {}
    for i in items:
        if i.column_name is None:
            tables[(i.schema_name, i.table_name)] = SchemaTableOut(
                id=i.id,
                schema_name=i.schema_name,
                name=i.table_name,
                kind=i.data_type,
                row_count=i.row_count,
                description=i.description,
                hidden=i.hidden,
                columns=[],
            )
    for i in items:
        if i.column_name is not None and (i.schema_name, i.table_name) in tables:
            tables[(i.schema_name, i.table_name)].columns.append(
                SchemaColumnOut(
                    id=i.id,
                    name=i.column_name,
                    data_type=i.data_type,
                    is_primary_key=i.is_primary_key,
                    foreign_key=i.foreign_key,
                    description=i.description,
                    sample_values=list(i.sample_values or []),
                    hidden=i.hidden,
                )
            )
    return list(tables.values())
