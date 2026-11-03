"""MCP server: the same read-only tools, for Claude Desktop, IDEs and any MCP client.

    datachat-agent mcp --api-key dc_live_...           (or DATACHAT_API_KEY in the environment)

The API key decides which account's database and documents the tools can see; it needs the
`ask` scope. Every tool only reads; run_sql goes through the same validator and read-only
executor as the API.
"""

import uuid
from typing import Any

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import select

from datachat_agent.agent import tools as t
from datachat_agent.security import Scope, hash_api_key
from datachat_agent.server.db import get_sessionmaker
from datachat_agent.server.limits import enforce_rate_limit, enforce_token_budget
from datachat_agent.server.models import ApiKey, Connection, Tenant

INSTRUCTIONS = """Tools for a company's SQL database and uploaded PDFs, read-only.
Start with list_tables, then describe_table, then run_sql with one SELECT. For PDFs use
search_documents (passages with file and page) or get_document_items (invoice line items).
`ask` answers a whole question in plain English with sources."""


class AccessError(ToolError):
    """Shown to the MCP client as a normal tool error with this message."""


async def _resolve(api_key: str, connection_id: str | None):
    """The account behind the key, and the database it uses (if any)."""
    async with get_sessionmaker()() as s:
        row = (
            await s.execute(
                select(ApiKey, Tenant.rate_limit_per_minute, Tenant.monthly_token_limit)
                .join(Tenant, Tenant.id == ApiKey.tenant_id)
                .where(ApiKey.key_hash == hash_api_key(api_key))
            )
        ).first()
        if row is None or row[0].revoked:
            raise AccessError("Invalid or revoked API key.")
        key, per_minute, monthly = row
        if key.key_type == "publishable":
            raise AccessError("Use a secret key (dc_live_...) for the MCP server.")
        if Scope.ADMIN.value not in key.scopes and Scope.ASK.value not in key.scopes:
            raise AccessError("The API key needs the 'ask' scope.")
        q = select(Connection.id).where(
            Connection.tenant_id == key.tenant_id,
            Connection.status == "ok",
            Connection.last_scanned_at.is_not(None),
        )
        if connection_id:
            q = q.where(Connection.id == uuid.UUID(connection_id))
        ready = list(await s.scalars(q))
        if connection_id and not ready:
            raise AccessError("That connection is not ready (test and scan it first).")
        if len(ready) > 1:
            raise AccessError("Several databases are connected; pass --connection-id.")
        return key.id, key.tenant_id, per_minute, monthly, (ready[0] if ready else None)


def build_server(
    api_key: str, connection_id: str | None = None, llm=None, embedder=None
) -> MCPServer:
    server = MCPServer(name="DataChat Agent", instructions=INSTRUCTIONS)
    state: dict[str, Any] = {}

    async def context():
        if not state:
            key_id, tenant_id, per_minute, monthly, conn_id = await _resolve(api_key, connection_id)
            state.update(
                key_id=key_id,
                tenant_id=tenant_id,
                per_minute=per_minute,
                monthly=monthly,
                conn_id=conn_id,
            )
        try:
            await enforce_rate_limit(state["key_id"], state["per_minute"])
        except HTTPException as e:
            raise ToolError(str(e.detail)) from None
        return state

    def get_embedder():
        if embedder is not None:
            return embedder
        from datachat_agent.core.embeddings import get_embedder as default

        return default()

    def get_llm():
        if llm is not None:
            return llm
        from datachat_agent.core.llm import get_chat_model

        return get_chat_model()

    async def run(fn, *args):
        st = await context()
        async with get_sessionmaker()() as s:
            conn = await s.get(Connection, st["conn_id"]) if st["conn_id"] else None
            ctx = t.ToolContext(s, conn, {}, st["tenant_id"], get_embedder())
            return await fn(ctx, *args)

    @server.tool(description=t.list_tables.__doc__)
    async def list_tables() -> dict[str, Any]:
        return {"tables": await run(t.list_tables)}

    @server.tool(description=t.describe_table.__doc__)
    async def describe_table(table: str) -> dict[str, Any]:
        return await run(t.describe_table, table)

    @server.tool(description=t.run_sql.__doc__)
    async def run_sql(sql: str) -> dict[str, Any]:
        return await run(t.run_sql, sql)

    @server.tool(description=t.search_documents.__doc__)
    async def search_documents(query: str) -> dict[str, Any]:
        return await run(t.search_documents, query)

    @server.tool(description=t.get_document_items.__doc__)
    async def get_document_items(
        document: str | None = None, item: str | None = None
    ) -> dict[str, Any]:
        return await run(t.get_document_items, document, item)

    @server.tool(
        description="Answer a question in plain English from the database and PDFs, "
        "with the SQL used and the file and page of every document fact."
    )
    async def ask(question: str, session_id: str | None = None) -> dict[str, Any]:
        from datachat_agent.agent.service import answer_and_log

        st = await context()
        async with get_sessionmaker()() as s:
            try:
                await enforce_token_budget(s, st["tenant_id"], st["monthly"])
            except HTTPException as e:
                raise ToolError(str(e.detail)) from None
            conn = await s.get(Connection, st["conn_id"]) if st["conn_id"] else None
            result, chat, _ = await answer_and_log(
                s,
                st["tenant_id"],
                conn,
                question,
                get_llm(),
                get_embedder(),
                session_id,
                None,
                st["key_id"],
            )
        data = result.sql.result if result.sql else None
        return {
            "answer": result.answer,
            "route": result.route,
            "sql": result.sql.sql if result.sql and data else None,
            "rows": data.rows[:50] if data else [],
            "row_count": data.row_count if data else 0,
            "citations": [
                {k: c[k] for k in ("filename", "page", "heading")} for c in result.citations
            ],
            "session_id": chat.client_key,
            "error": result.error,
        }

    return server
