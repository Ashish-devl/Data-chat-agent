"""Engines for customer databases, and layer 1 of the safety model: the user must be read-only."""

import hashlib
import ipaddress
import re
import socket
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


# Query options that would let a connection string point somewhere other than its host.
_REDIRECTING_OPTIONS = {"host", "hostaddr", "service", "unix_socket", "socket", "passfile"}


def public_address(host: str | None) -> str:
    """Resolve a host and refuse it unless every address is public. Returns one to connect to.

    Used in hosted mode (BLOCK_PRIVATE_NETWORKS=true) so a customer's connection string cannot
    reach the server's own network: localhost, 10.x, 192.168.x, cloud metadata addresses...
    """
    if not host:
        raise ConnectionConfigError("The connection string must name a host.")
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise ConnectionConfigError(f"Cannot find the host '{host}'.") from None
    addresses = sorted({info[4][0] for info in infos})
    for raw in addresses:
        addr = ipaddress.ip_address(raw.split("%", 1)[0])
        if not addr.is_global or addr.is_multicast:
            raise ConnectionConfigError(
                f"'{host}' points to an internal network address, which is not allowed here."
            )
    return addresses[0]


def check_dsn_allowed(dsn: str) -> None:
    """In hosted mode: the connection string must point at a public address and nowhere else."""
    from datachat_agent.config import get_settings

    if not get_settings().block_private_networks:
        return
    url = make_url(normalize_dsn(dsn)[1])
    if _REDIRECTING_OPTIONS & {k.lower() for k in url.query}:
        raise ConnectionConfigError("Connection options that change the host are not allowed.")
    public_address(url.host)


def engine_options(dialect: str, url: str) -> tuple[str, dict]:
    """The URL and driver options for an engine; in hosted mode, pinned to a checked address."""
    from datachat_agent.config import get_settings

    if dialect == "postgresql":
        connect_args = {"connect_timeout": 5, "application_name": "datachat-agent"}
    else:
        connect_args = {"connect_timeout": 5, "read_timeout": 60, "charset": "utf8mb4"}
    if get_settings().block_private_networks:
        check_dsn_allowed(url)
        parsed = make_url(url)
        ip = public_address(parsed.host)
        # Connect to the address that was checked, so a later DNS change cannot redirect it.
        if dialect == "postgresql":
            connect_args["hostaddr"] = ip  # host stays for TLS certificate checks
        else:
            url = parsed.set(host=ip).render_as_string(hide_password=False)
    return url, connect_args


def get_engine(dsn: str) -> Engine:
    dialect, url = normalize_dsn(dsn)
    key = hashlib.sha256(url.encode()).hexdigest()
    with _lock:
        engine = _engines.get(key)
        if engine is None:
            url, connect_args = engine_options(dialect, url)
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
