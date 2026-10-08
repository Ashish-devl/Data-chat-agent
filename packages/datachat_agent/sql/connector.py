"""Engines for customer databases, and layer 1 of the safety model: the user must be read-only."""

import hashlib
import re
import threading
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

SUPPORTED_DIALECTS = ("postgresql", "mysql")
_DRIVERS = {"postgresql": "postgresql+psycopg", "mysql": "mysql+pymysql"}

_engines: dict[str, Engine] = {}
_lock = threading.Lock()


class ConnectionConfigError(ValueError):
    pass


def normalize_dsn(dsn: str) -> tuple[str, str]:
    """Return (dialect, SQLAlchemy URL with our driver) for a user-supplied connection string."""
    try:
        url = make_url(dsn.strip())
    except Exception:
        raise ConnectionConfigError("The connection string is not a valid database URL.") from None
    backend = url.get_backend_name()
    if backend in ("postgres", "postgresql"):
        dialect = "postgresql"
    elif backend in ("mysql", "mariadb"):
        dialect = "mysql"
    else:
        raise ConnectionConfigError("Only PostgreSQL and MySQL connection strings are supported.")
    if not url.database:
        raise ConnectionConfigError("The connection string must name a database.")
    url = url.set(drivername=_DRIVERS[dialect])
    return dialect, url.render_as_string(hide_password=False)


def get_engine(dsn: str) -> Engine:
    dialect, url = normalize_dsn(dsn)
    key = hashlib.sha256(url.encode()).hexdigest()
    with _lock:
        engine = _engines.get(key)
        if engine is None:
            if dialect == "postgresql":
                connect_args = {"connect_timeout": 5, "application_name": "datachat-agent"}
            else:
                connect_args = {"connect_timeout": 5, "read_timeout": 60, "charset": "utf8mb4"}
            engine = create_engine(
                url,
                pool_size=2,
                max_overflow=3,
                pool_pre_ping=True,
                pool_recycle=1800,
                connect_args=connect_args,
            )
            _engines[key] = engine
    return engine


def dispose_engine(dsn: str) -> None:
    _, url = normalize_dsn(dsn)
    with _lock:
        engine = _engines.pop(hashlib.sha256(url.encode()).hexdigest(), None)
    if engine is not None:
        engine.dispose()


@dataclass(frozen=True)
class ReadOnlyCheck:
    ok: bool
    detail: str


def check_read_only(engine: Engine, dialect: str) -> ReadOnlyCheck:
    """Connect and confirm the user cannot write. Any error counts as a failure (fail closed)."""
    try:
        with engine.connect() as conn:
            if dialect == "postgresql":
                return _check_postgres(conn)
            return _check_mysql(conn)
    except Exception as e:  # noqa: BLE001 - every failure must refuse the connection
        return ReadOnlyCheck(False, f"Could not connect: {_first_line(e)}")


_PG_WRITABLE_TABLES = text("""
    SELECT n.nspname || '.' || c.relname
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
      AND (has_table_privilege(c.oid, 'INSERT') OR has_table_privilege(c.oid, 'UPDATE')
           OR has_table_privilege(c.oid, 'DELETE') OR has_table_privilege(c.oid, 'TRUNCATE'))
    ORDER BY 1 LIMIT 5
""")

_PG_CREATABLE_SCHEMAS = text("""
    SELECT nspname FROM pg_namespace
    WHERE nspname NOT IN ('pg_catalog', 'information_schema')
      AND nspname NOT LIKE 'pg_toast%' AND nspname NOT LIKE 'pg_temp%'
      AND has_schema_privilege(oid, 'CREATE')
    ORDER BY 1 LIMIT 5
""")


def _check_postgres(conn) -> ReadOnlyCheck:
    user, is_super = conn.execute(
        text("SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user")
    ).one()
    if is_super:
        return ReadOnlyCheck(False, f"User '{user}' is a superuser. Use a read-only user.")
    tables = conn.execute(_PG_WRITABLE_TABLES).scalars().all()
    if tables:
        return ReadOnlyCheck(
            False,
            f"User '{user}' can write to {', '.join(tables)}. Grant SELECT only, e.g. "
            f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM {user};",
        )
    schemas = conn.execute(_PG_CREATABLE_SCHEMAS).scalars().all()
    if schemas:
        return ReadOnlyCheck(
            False,
            f"User '{user}' can create tables in schema {', '.join(schemas)}. Fix with "
            f"REVOKE CREATE ON SCHEMA {schemas[0]} FROM PUBLIC, {user};",
        )
    can_create_db_objects = conn.execute(
        text("SELECT has_database_privilege(current_database(), 'CREATE')")
    ).scalar()
    if can_create_db_objects:
        return ReadOnlyCheck(False, f"User '{user}' can create schemas in this database.")
    return ReadOnlyCheck(True, f"Connected as '{user}', read-only.")


_MYSQL_ALLOWED_PRIVILEGES = {"SELECT", "SHOW VIEW", "USAGE", "SHOW DATABASES"}
_GRANT_RE = re.compile(r"^GRANT (?P<privs>.+?) ON (?P<target>\S+) TO ", re.IGNORECASE)


def _check_mysql(conn) -> ReadOnlyCheck:
    user = conn.execute(text("SELECT CURRENT_USER()")).scalar()
    grants = conn.execute(text("SHOW GRANTS FOR CURRENT_USER()")).scalars().all()
    for grant in grants:
        match = _GRANT_RE.match(grant)
        if not match:
            # Role grants and anything we do not understand fail closed.
            return ReadOnlyCheck(False, f"User '{user}' has a grant we cannot verify: {grant}")
        privileges = {p.strip().split("(")[0].strip().upper() for p in match["privs"].split(",")}
        extra = privileges - _MYSQL_ALLOWED_PRIVILEGES
        if extra:
            return ReadOnlyCheck(
                False,
                f"User '{user}' has {', '.join(sorted(extra))} on {match['target']}. "
                "Grant SELECT only.",
            )
    return ReadOnlyCheck(True, f"Connected as '{user}', read-only.")


def _first_line(e: BaseException) -> str:
    orig = getattr(e, "orig", None) or e
    return str(orig).strip().splitlines()[0] if str(orig).strip() else type(e).__name__
