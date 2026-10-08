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
    """Create the pgvector extension and all tables."""
    from datachat_agent.server.db import init_db as _init_db

    asyncio.run(_init_db())
    typer.echo("Database ready.")


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


if __name__ == "__main__":
    app()
