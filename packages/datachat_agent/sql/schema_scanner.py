"""Read a customer database's structure: tables, columns, keys, row counts and sample values."""

from dataclasses import dataclass, field

from sqlalchemy import Engine, Enum, String, Text, inspect, text

from datachat_agent.sql.executor import QueryExecutionError, execute_readonly

SAMPLE_VALUES = 5
SAMPLE_MAX_CHARS = 80
EXACT_COUNT_BELOW = 10_000


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    is_primary_key: bool = False
    foreign_key: str | None = None  # "schema.table.column"
    samples: list[str] = field(default_factory=list)


@dataclass
class TableInfo:
    schema: str
    name: str
    is_view: bool
    row_count: int | None
    columns: list[ColumnInfo]


def scan_schema(
    engine: Engine,
    dialect: str,
    schemas: list[str] | None = None,
    hidden: set[tuple[str, str, str | None]] | None = None,
) -> list[TableInfo]:
    """`hidden` holds (schema, table, column) already hidden by an admin; column None = whole table.

    Hidden items are still listed (so the admin keeps the flag) but never sampled.
    """
    hidden = hidden or set()
    insp = inspect(engine)
    schemas = schemas or [insp.default_schema_name]
    tables: list[TableInfo] = []

    for schema in schemas:
        estimates = _row_estimates(engine, dialect, schema)
        names = [(n, False) for n in insp.get_table_names(schema=schema)]
        names += [(n, True) for n in insp.get_view_names(schema=schema)]
        for name, is_view in sorted(names):
            table_hidden = (schema, name, None) in hidden
            pk = set(insp.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])
            fks: dict[str, str] = {}
            for fk in insp.get_foreign_keys(name, schema=schema):
                target_schema = fk.get("referred_schema") or schema
                for local, remote in zip(
                    fk["constrained_columns"], fk["referred_columns"], strict=False
                ):
                    fks[local] = f"{target_schema}.{fk['referred_table']}.{remote}"

            columns = []
            for col in insp.get_columns(name, schema=schema):
                info = ColumnInfo(
                    name=col["name"],
                    data_type=_type_name(col["type"]),
                    is_primary_key=col["name"] in pk,
                    foreign_key=fks.get(col["name"]),
                )
                is_text = isinstance(col["type"], (String, Text, Enum))
                if is_text and not table_hidden and (schema, name, col["name"]) not in hidden:
                    info.samples = _samples(engine, dialect, schema, name, col["name"])
                columns.append(info)

            row_count = None if is_view else estimates.get(name)
            if (
                not is_view
                and not table_hidden
                and (row_count is None or row_count < EXACT_COUNT_BELOW)
            ):
                row_count = _exact_count(engine, dialect, schema, name) or row_count
            tables.append(TableInfo(schema, name, is_view, row_count, columns))
    return tables


def _type_name(sa_type) -> str:
    try:
        return str(sa_type).lower()
    except Exception:  # noqa: BLE001 - some reflected types cannot render
        return type(sa_type).__name__.lower()


def _qualified(engine: Engine, schema: str, table: str) -> str:
    q = engine.dialect.identifier_preparer
    return f"{q.quote_schema(schema)}.{q.quote(table)}"


def _samples(engine: Engine, dialect: str, schema: str, table: str, column: str) -> list[str]:
    col = engine.dialect.identifier_preparer.quote(column)
    sql = (
        f"SELECT DISTINCT {col} AS v FROM {_qualified(engine, schema, table)} "
        f"WHERE {col} IS NOT NULL LIMIT {SAMPLE_VALUES}"
    )
    try:
        rows = execute_readonly(engine, dialect, sql, timeout_seconds=5).rows
    except QueryExecutionError:
        return []
    return [str(r["v"])[:SAMPLE_MAX_CHARS] for r in rows]


def _exact_count(engine: Engine, dialect: str, schema: str, table: str) -> int | None:
    try:
        rows = execute_readonly(
            engine,
            dialect,
            f"SELECT COUNT(*) AS n FROM {_qualified(engine, schema, table)}",
            timeout_seconds=5,
        ).rows
        return int(rows[0]["n"])
    except QueryExecutionError:
        return None


def _row_estimates(engine: Engine, dialect: str, schema: str) -> dict[str, int]:
    if dialect == "postgresql":
        sql = text("""
            SELECT c.relname, c.reltuples::bigint FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :s AND c.relkind IN ('r', 'p')
        """)
    else:
        sql = text(
            "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA = :s"
        )
    try:
        with engine.connect() as conn:
            return {
                name: int(n)
                for name, n in conn.execute(sql, {"s": schema})
                if n is not None and n >= 0
            }
    except Exception:  # noqa: BLE001 - estimates are optional
        return {}
