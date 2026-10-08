"""Integration tests run against the Docker databases (docker compose up -d db; MySQL optional).

They create a throwaway service database (datachat_test) and load the made-up erp_demo
customer database. Without a reachable Postgres they are skipped.
"""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.conftest import TEST_DATABASE_URL

PG_ADMIN_URL = os.environ.get(
    "TEST_PG_ADMIN_URL", "postgresql://datachat:datachat@localhost:5432/postgres"
)
MYSQL_ADMIN_URL = os.environ.get(
    "TEST_MYSQL_ADMIN_URL", "mysql://root:rootpass@localhost:3307/mysql"
)
PG_HOST = make_url(PG_ADMIN_URL).host
PG_PORT = make_url(PG_ADMIN_URL).port or 5432
MY_HOST = make_url(MYSQL_ADMIN_URL).host
MY_PORT = make_url(MYSQL_ADMIN_URL).port or 3306

PG_READONLY = f"postgresql://erp_readonly:erp_readonly@{PG_HOST}:{PG_PORT}/erp_demo"
PG_WRITER = f"postgresql://erp_writer:erp_writer@{PG_HOST}:{PG_PORT}/erp_demo"
MY_READONLY = f"mysql://erp_readonly:erp_readonly@{MY_HOST}:{MY_PORT}/erp_demo"
MY_WRITER = f"mysql://erp_writer:erp_writer@{MY_HOST}:{MY_PORT}/erp_demo"


def _reachable(url: str) -> bool:
    u = make_url(url)
    driver = (
        "postgresql+psycopg" if u.get_backend_name().startswith("postgres") else "mysql+pymysql"
    )
    try:
        engine = create_engine(u.set(drivername=driver), connect_args={"connect_timeout": 3})
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


def _load_erp(admin_url: str) -> None:
    path = Path(__file__).resolve().parents[2] / "eval" / "datasets" / "erp_demo.py"
    spec = importlib.util.spec_from_file_location("erp_demo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.load(admin_url)


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def databases():
    if not _reachable(PG_ADMIN_URL):
        pytest.skip("Postgres not reachable; run: docker compose up -d db")
    admin = create_engine(
        make_url(PG_ADMIN_URL).set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT"
    )
    name = make_url(TEST_DATABASE_URL).database
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {name}"))
    admin.dispose()

    from datachat_agent.server.db import run_migrations

    run_migrations()
    _load_erp(PG_ADMIN_URL)
    has_mysql = _reachable(MYSQL_ADMIN_URL)
    if has_mysql:
        _load_erp(MYSQL_ADMIN_URL)
    return {"mysql": has_mysql}


@pytest.fixture(scope="session")
def client(databases):
    from datachat_agent.server.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def mysql_available(databases):
    if not databases["mysql"]:
        pytest.skip("MySQL not reachable; run: docker compose --profile dev up -d mysql")


def make_tenant(client: TestClient, name: str | None = None) -> dict[str, str]:
    """Create a tenant with an admin key, inside the app's event loop. Returns auth headers."""

    async def _create() -> str:
        from datachat_agent.security import Scope, generate_api_key
        from datachat_agent.server.db import get_sessionmaker
        from datachat_agent.server.models import ApiKey, Tenant

        async with get_sessionmaker()() as session:
            tenant = Tenant(name=name or f"t-{uuid.uuid4().hex[:6]}")
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

    key = client.portal.call(_create)
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def admin(client):
    return make_tenant(client)


def add_connection(client: TestClient, headers: dict, dsn: str, **extra) -> dict:
    r = client.post("/v1/connections", headers=headers, json={"name": "erp", "dsn": dsn, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def scanned_connection(client: TestClient, headers: dict, dsn: str, **extra) -> dict:
    conn = add_connection(client, headers, dsn, **extra)
    r = client.post(f"/v1/connections/{conn['id']}/scan", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["connection"]
