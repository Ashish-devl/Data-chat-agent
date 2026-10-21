"""The fixed tool set. Every tool only reads; the same functions back the MCP server (week 6)."""

from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from datachat_agent.config import get_settings
from datachat_agent.security import decrypt
from datachat_agent.server.models import Connection, SchemaItem
from datachat_agent.sql.connector import get_engine
from datachat_agent.sql.executor import QueryExecutionError, execute_readonly
from datachat_agent.sql.schema_store import build_policy
from datachat_agent.sql.validator import SQLValidationError, validate_sql

TOOL_NAMES = ["list_tables", "describe_table", "run_sql", "search_documents", "get_document_items"]


@dataclass
class ToolContext:
    session: AsyncSession
    connection: Connection | None
    user_context: dict[str, Any] = field(default_factory=dict)


async def _visible_items(ctx: ToolContext) -> list[SchemaItem]:
    if ctx.connection is None:
        return []
    items = list(
        await ctx.session.scalars(
            select(SchemaItem)
            .where(SchemaItem.connection_id == ctx.connection.id)
            .order_by(SchemaItem.schema_name, SchemaItem.table_name, SchemaItem.column_name)
        )
    )
    hidden = {(i.schema_name, i.table_name) for i in items if i.column_name is None and i.hidden}
    return [i for i in items if not i.hidden and (i.schema_name, i.table_name) not in hidden]


async def list_tables(ctx: ToolContext) -> list[dict[str, Any]]:
    """Tables the user may query, with descriptions and approximate row counts."""
    return [
        {
            "table": f"{i.schema_name}.{i.table_name}",
            "description": i.description,
            "row_count": i.row_count,
        }
        for i in await _visible_items(ctx)
        if i.column_name is None
    ]


async def describe_table(ctx: ToolContext, table: str) -> dict[str, Any]:
    """Columns of one table: type, keys, description, examples. Hidden columns are left out."""
    schema, _, name = table.rpartition(".")
    items = [
        i
        for i in await _visible_items(ctx)
        if i.table_name.lower() == name.lower()
        and (not schema or i.schema_name.lower() == schema.lower())
    ]
    tables = {(i.schema_name, i.table_name) for i in items if i.column_name is None}
    if len(tables) != 1:
        return {"error": f"Unknown or ambiguous table '{table}'. Use list_tables."}
    columns = [
        {
            "column": i.column_name,
            "type": i.data_type,
            "primary_key": i.is_primary_key,
            "references": i.foreign_key,
            "description": i.description,
            "examples": i.sample_values,
        }
        for i in items
        if i.column_name is not None
    ]
    schema_name, table_name = next(iter(tables))
    return {"table": f"{schema_name}.{table_name}", "columns": columns}


async def run_sql(ctx: ToolContext, sql: str) -> dict[str, Any]:
    """Run one read-only SELECT through the validator and the read-only executor."""
    if ctx.connection is None:
        return {"error": "No database is connected."}
    settings = get_settings()
    policy = await build_policy(ctx.session, ctx.connection)
    try:
        q = validate_sql(
            sql, ctx.connection.dialect, policy, settings.sql_max_rows, ctx.user_context
        )
        engine = get_engine(decrypt(ctx.connection.dsn_encrypted))
        r = await run_in_threadpool(
            execute_readonly,
            engine,
            ctx.connection.dialect,
            q.sql,
            settings.sql_timeout_seconds,
            settings.sql_max_rows,
            settings.sql_max_response_bytes,
        )
    except (SQLValidationError, QueryExecutionError) as e:
        return {"error": str(e)}
    return {
        "sql": q.sql,
        "columns": r.columns,
        "rows": r.rows,
        "row_count": r.row_count,
        "truncated": r.truncated,
    }


async def search_documents(ctx: ToolContext, query: str) -> dict[str, Any]:
    """Search uploaded PDFs. Available once documents are supported (week 4)."""
    return {"results": [], "note": "Document search is not available yet."}


async def get_document_items(
    ctx: ToolContext, document: str | None = None, item: str | None = None
) -> dict[str, Any]:
    """Line items extracted from PDF tables. Available once documents are supported (week 4)."""
    return {"items": [], "note": "Document line items are not available yet."}


class _TableArg(BaseModel):
    table: str = Field(description="Table name, optionally schema-qualified")


class _SqlArg(BaseModel):
    sql: str = Field(description="One read-only SELECT statement")


class _QueryArg(BaseModel):
    query: str = Field(description="What to look for in the documents")


class _ItemsArg(BaseModel):
    document: str | None = Field(default=None, description="Document name filter")
    item: str | None = Field(default=None, description="Item name filter")


def build_tools(ctx: ToolContext) -> list[StructuredTool]:
    """LangChain tools bound to one request's context (connection, user context)."""

    async def _list() -> list[dict[str, Any]]:
        return await list_tables(ctx)

    async def _describe(table: str) -> dict[str, Any]:
        return await describe_table(ctx, table)

    async def _run(sql: str) -> dict[str, Any]:
        return await run_sql(ctx, sql)

    async def _search(query: str) -> dict[str, Any]:
        return await search_documents(ctx, query)

    async def _items(document: str | None = None, item: str | None = None) -> dict[str, Any]:
        return await get_document_items(ctx, document, item)

    return [
        StructuredTool.from_function(
            coroutine=_list, name="list_tables", description=list_tables.__doc__
        ),
        StructuredTool.from_function(
            coroutine=_describe,
            name="describe_table",
            description=describe_table.__doc__,
            args_schema=_TableArg,
        ),
        StructuredTool.from_function(
            coroutine=_run, name="run_sql", description=run_sql.__doc__, args_schema=_SqlArg
        ),
        StructuredTool.from_function(
            coroutine=_search,
            name="search_documents",
            description=search_documents.__doc__,
            args_schema=_QueryArg,
        ),
        StructuredTool.from_function(
            coroutine=_items,
            name="get_document_items",
            description=get_document_items.__doc__,
            args_schema=_ItemsArg,
        ),
    ]
