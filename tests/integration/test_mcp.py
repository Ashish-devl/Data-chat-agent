"""The MCP server, driven in-process by the official MCP client."""

import asyncio
import json

from mcp.client import Client

from datachat_agent.server import db
from tests.fakes import ScriptedChat, sql
from tests.integration.conftest import PG_READONLY, scanned_connection

TOOLS = {
    "list_tables",
    "describe_table",
    "run_sql",
    "search_documents",
    "get_document_items",
    "ask",
}


def key_of(headers) -> str:
    return headers["Authorization"].split()[1]


def payload(result) -> dict:
    if getattr(result, "structured_content", None):
        return result.structured_content
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    return json.loads(result.content[0].text)


def session(server, steps):
    """Run several tool calls through one client on one event loop, like a real MCP host."""

    async def _go():
        async with Client(server) as c:
            out = []
            for tool, args in steps:
                out.append(
                    await c.list_tools() if tool is None else await c.call_tool(tool, args or {})
                )
            return out

    # The MCP server runs on its own event loop: give it its own database engine for the run.
    saved = db._engine, db._sessionmaker
    db._engine = db._sessionmaker = None

    async def _run():
        try:
            return await _go()
        finally:
            await db.dispose_engine()

    try:
        return asyncio.run(_run())
    finally:
        db._engine, db._sessionmaker = saved


def test_tools_over_mcp(client, admin):
    from datachat_agent.mcp_server import build_server

    scanned_connection(client, admin, PG_READONLY)
    llm = ScriptedChat([sql("SELECT COUNT(*) AS n FROM public.suppliers"), "Eight suppliers."])
    listed, tables, cols, rows, refused, answer = session(
        build_server(key_of(admin), llm=llm),
        [
            (None, None),
            ("list_tables", {}),
            ("describe_table", {"table": "suppliers"}),
            ("run_sql", {"sql": "SELECT COUNT(*) AS n FROM buyers"}),
            ("run_sql", {"sql": "DELETE FROM buyers"}),
            ("ask", {"question": "How many suppliers?"}),
        ],
    )
    assert {t.name for t in listed.tools} == TOOLS
    assert len(payload(tables)["tables"]) == 8
    assert "city" in [c["column"] for c in payload(cols)["columns"]]
    assert payload(rows)["rows"] == [{"n": 6}]
    assert "Only SELECT" in payload(refused)["error"]
    assert payload(answer)["answer"] == "Eight suppliers."
    assert payload(answer)["rows"] == [{"n": 8}]


def test_mcp_refuses_bad_and_publishable_keys(client, admin):
    from datachat_agent.mcp_server import build_server

    r = client.post(
        "/v1/keys",
        headers=admin,
        json={"name": "w", "type": "publishable", "allowed_domains": ["x.com"]},
    )
    pub = r.json()["key"]
    for key, message in [("dc_live_wrong", "Invalid or revoked"), (pub, "secret key")]:
        (result,) = session(build_server(key), [("list_tables", {})])
        assert getattr(result, "is_error", None) or getattr(result, "isError", None)
        assert message in result.content[0].text
