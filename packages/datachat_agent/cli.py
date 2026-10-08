import asyncio

import typer

app = typer.Typer(help="DataChat Agent command line", no_args_is_help=True)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the API server."""
    import uvicorn

    uvicorn.run("datachat_agent.server.app:app", host=host, port=port, reload=reload)


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
