import asyncio

import typer

app = typer.Typer(help="DataChat Agent command line", no_args_is_help=True)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the API server."""
    import uvicorn

    uvicorn.run("datachat_agent.server.app:app", host=host, port=port, reload=reload)


@app.command()
def worker() -> None:
    """Run the background worker (set JOBS_BACKEND=arq; needs Redis)."""
    from arq import run_worker

    from datachat_agent.server.worker import WorkerSettings

    run_worker(WorkerSettings)


@app.command()
def mcp(
    api_key: str = typer.Option(
        ..., envvar="DATACHAT_API_KEY", help="A secret key with the 'ask' scope"
    ),
    connection_id: str | None = typer.Option(None, help="Which database, if several"),
) -> None:
    """Run the MCP server over stdio (for Claude Desktop and other MCP clients)."""
    from datachat_agent.mcp_server import build_server

    build_server(api_key, connection_id).run("stdio")


@app.command()
def scan(connection_id: str) -> None:
    """Re-read a connection's schema now (checks the user is read-only first)."""
    import uuid

    async def _run():
        from datachat_agent.server.db import get_sessionmaker
        from datachat_agent.server.models import Connection
        from datachat_agent.server.services import scan_connection

        async with get_sessionmaker()() as session:
            conn = await session.get(Connection, uuid.UUID(connection_id))
            if conn is None:
                raise typer.BadParameter("No connection with that id.")
            return await scan_connection(session, conn)

    counts = asyncio.run(_run())
    typer.echo(
        f"{counts['tables']} tables, {counts['columns']} columns ({counts['removed']} removed)"
    )


@app.command()
def ingest(tenant_id: str, files: list[str]) -> None:
    """Upload PDFs for a tenant and process them now."""
    import uuid
    from pathlib import Path

    async def _run():
        from datachat_agent.config import get_settings
        from datachat_agent.core.embeddings import get_embedder
        from datachat_agent.docs.ingest import UploadRejected, ingest_document, store_document
        from datachat_agent.server.db import get_sessionmaker
        from datachat_agent.server.jobs import _llm_or_none

        results = []
        async with get_sessionmaker()() as session:
            for f in files:
                path = Path(f)
                try:
                    stored = await store_document(
                        session,
                        uuid.UUID(tenant_id),
                        path.name,
                        path.read_bytes(),
                        get_settings().max_pdf_mb * 1_000_000,
                    )
                except UploadRejected as e:
                    results.append((path.name, "rejected", str(e)))
                    continue
                doc = stored.document
                if stored.created:
                    doc = await ingest_document(session, doc.id, get_embedder(), _llm_or_none())
                results.append(
                    (path.name, doc.status if stored.created else "duplicate", doc.error or "")
                )
        return results

    for name, status, detail in asyncio.run(_run()):
        typer.echo(f"{name}: {status} {detail}".rstrip())


@app.command("export-feedback")
def export_feedback_cmd(
    tenant_id: str,
    rating: str = typer.Option("wrong", help="wrong, helpful or all"),
    out: str | None = typer.Option(None, help="Write to this file instead of the screen"),
) -> None:
    """Rated answers as candidate evaluation questions (JSON)."""
    import json
    import uuid
    from pathlib import Path

    async def _run():
        from datachat_agent.agent.service import export_feedback
        from datachat_agent.server.db import get_sessionmaker

        async with get_sessionmaker()() as session:
            return await export_feedback(
                session, uuid.UUID(tenant_id), None if rating == "all" else rating
            )

    text = json.dumps(asyncio.run(_run()), indent=2, ensure_ascii=False)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        typer.echo(f"Wrote {out}")
    else:
        typer.echo(text)


@app.command("init-db")
def init_db() -> None:
    """Create or upgrade all tables (runs database migrations)."""
    from datachat_agent.server.db import run_migrations

    run_migrations()
    typer.echo("Database ready.")


@app.command("make-migration", hidden=True)
def make_migration(message: str) -> None:
    """Developers: generate a migration from changes to server/models.py."""
    from alembic import command

    from datachat_agent.server.db import alembic_config

    command.revision(alembic_config(), message=message, autogenerate=True)


@app.command("create-tenant")
def create_tenant(name: str) -> None:
    """Create a tenant and its first admin API key."""

    async def _run() -> str:
        from datachat_agent.security import Scope, generate_api_key
        from datachat_agent.server.db import get_sessionmaker
        from datachat_agent.server.models import ApiKey, Tenant

        async with get_sessionmaker()() as session:
            tenant = Tenant(name=name)
            session.add(tenant)
            await session.flush()
            key, prefix, key_hash = generate_api_key()
            session.add(
                ApiKey(
                    tenant_id=tenant.id,
                    name="admin",
                    key_hash=key_hash,
                    prefix=prefix,
                    scopes=[Scope.ADMIN.value],
                )
            )
            await session.commit()
            return key

    key = asyncio.run(_run())
    typer.echo(f"Tenant '{name}' created.\nAdmin API key (shown once, store it now):\n{key}")


@app.command("list-tenants")
def list_tenants() -> None:
    """Show every tenant with its limits and this month's token use."""

    async def _run():
        from sqlalchemy import select

        from datachat_agent.server.db import get_sessionmaker
        from datachat_agent.server.limits import tokens_this_month
        from datachat_agent.server.models import Tenant

        async with get_sessionmaker()() as session:
            tenants = list(await session.scalars(select(Tenant).order_by(Tenant.created_at)))
            return [(t, await tokens_this_month(session, t.id)) for t in tenants]

    from datachat_agent.config import get_settings

    s = get_settings()
    for t, used in asyncio.run(_run()):
        rpm = t.rate_limit_per_minute or f"{s.default_rate_limit_per_minute} (default)"
        cap = (
            t.monthly_token_limit
            if t.monthly_token_limit is not None
            else (f"{s.default_monthly_token_limit or 'none'} (default)")
        )
        typer.echo(f"{t.id}  {t.name}  rpm={rpm}  monthly_tokens={cap}  used={used:,}")


@app.command("set-limits")
def set_limits(
    tenant_id: str,
    per_minute: int | None = typer.Option(None, help="Requests per minute per API key"),
    monthly_tokens: int | None = typer.Option(None, help="Model tokens per calendar month"),
    use_defaults: bool = typer.Option(False, help="Clear both limits and use server defaults"),
) -> None:
    """Set a tenant's limits (an operator task; tenants cannot raise their own)."""
    import uuid

    async def _run():
        from datachat_agent.server.db import get_sessionmaker
        from datachat_agent.server.models import Tenant

        async with get_sessionmaker()() as session:
            tenant = await session.get(Tenant, uuid.UUID(tenant_id))
            if tenant is None:
                raise typer.BadParameter("No tenant with that id.")
            if use_defaults:
                tenant.rate_limit_per_minute = tenant.monthly_token_limit = None
            if per_minute is not None:
                tenant.rate_limit_per_minute = per_minute
            if monthly_tokens is not None:
                tenant.monthly_token_limit = monthly_tokens
            await session.commit()
            return tenant.name, tenant.rate_limit_per_minute, tenant.monthly_token_limit

    name, rpm, cap = asyncio.run(_run())
    typer.echo(f"{name}: rpm={rpm or 'default'} monthly_tokens={cap or 'default'}")


@app.command("gen-secret")
def gen_secret() -> None:
    """Print a new SECRET_KEY for .env."""
    from cryptography.fernet import Fernet

    typer.echo(Fernet.generate_key().decode())


@app.command("check-llm")
def check_llm() -> None:
    """Send one test message to the LLM configured in .env."""
    from datachat_agent.config import get_settings
    from datachat_agent.core.llm import get_chat_model

    s = get_settings()
    url = s.llm_base_url or "default"
    typer.echo(f"Provider: {s.llm_provider}  Model: {s.llm_model}  URL: {url}")
    reply = get_chat_model().invoke("Reply with exactly: OK")
    typer.echo(f"Reply: {reply.content}")


@app.command("ask-sql")
def ask_sql(
    connection_id: str,
    question: str,
    context: list[str] = typer.Option(  # noqa: B008
        [], "--context", "-c", help="User context for row filters, e.g. -c user_company=7"
    ),
) -> None:
    """Try the SQL path on a scanned connection: question -> SQL -> rows."""
    import json
    import uuid

    from datachat_agent.core.embeddings import get_embedder
    from datachat_agent.core.llm import get_chat_model
    from datachat_agent.server.db import get_sessionmaker
    from datachat_agent.server.models import Connection
    from datachat_agent.sql.pipeline import answer_with_sql

    user_context: dict[str, object] = {}
    for pair in context:
        key, _, value = pair.partition("=")
        user_context[key] = int(value) if value.lstrip("-").isdigit() else value

    async def _run():
        async with get_sessionmaker()() as session:
            conn = await session.get(Connection, uuid.UUID(connection_id))
            if conn is None:
                raise typer.BadParameter("No connection with that id.")
            return await answer_with_sql(
                session, conn, question, get_chat_model(), get_embedder(), user_context
            )

    ans = asyncio.run(_run())
    typer.echo(f"Tables considered: {', '.join(ans.tables_considered)}")
    typer.echo(f"SQL ({ans.attempts} attempt(s)):")
    typer.echo(ans.sql or "-")
    if ans.cannot_answer:
        typer.echo(f"Cannot answer: {ans.cannot_answer}")
    elif ans.error:
        typer.echo(f"Error: {ans.error}")
    elif ans.result:
        r = ans.result
        typer.echo(f"{r.row_count} row(s){' (truncated)' if r.truncated else ''}:")
        for row in r.rows[:20]:
            typer.echo(json.dumps(row, ensure_ascii=False))
    typer.echo(f"Tokens: {ans.tokens}  Latency: {ans.latency_ms} ms")


if __name__ == "__main__":
    app()
