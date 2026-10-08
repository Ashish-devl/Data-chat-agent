"""Second safety layer: parse model-written SQL and allow only one read-only SELECT.

Layer 1 is the read-only database user (checked on connect); layer 3 is the read-only
transaction in the executor. This layer must hold even if the other two were missing.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

logging.getLogger("sqlglot").setLevel(logging.ERROR)

DIALECTS = {"postgresql": "postgres", "mysql": "mysql"}

# Node types that write, lock, change session state or run arbitrary commands.
_FORBIDDEN_NODES = tuple(
    getattr(exp, name)
    for name in (
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Drop",
        "Alter",
        "AlterTable",
        "TruncateTable",
        "Copy",
        "Command",
        "Into",
        "Lock",
        "Set",
        "Use",
        "Grant",
        "Transaction",
        "Commit",
        "Rollback",
        "Pragma",
        "LoadData",
        "Analyze",
        "Describe",
    )
    if hasattr(exp, name)
)

_FORBIDDEN_FUNCTIONS = {
    "sleep",
    "benchmark",
    "get_lock",
    "release_lock",
    "release_all_locks",
    "is_free_lock",
    "load_file",
    "master_pos_wait",
    "source_pos_wait",
    "sys_exec",
    "sys_eval",
    "nextval",
    "setval",
    "currval",
    "lastval",
    "set_config",
    "current_setting",
    "query_to_xml",
    "query_to_xml_and_xmlschema",
    "query_to_xmlschema",
    "table_to_xml",
    "table_to_xml_and_xmlschema",
    "database_to_xml",
    "schema_to_xml",
    "cursor_to_xml",
    "txid_current",
    "txid_current_if_assigned",
    "copy",
}
# Whole families: Postgres admin/file functions, large objects, dblink, extensions.
_FORBIDDEN_PREFIXES = ("pg_", "lo_", "dblink", "xp_", "sp_", "sys_", "file_", "http_")


class SQLValidationError(ValueError):
    """The query was refused. The message is safe to show and to send back to the model."""


@dataclass(frozen=True)
class TableRef:
    schema: str
    table: str

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"


@dataclass
class SchemaPolicy:
    """What a query may touch, built from the scanned (non-hidden) schema."""

    tables: set[TableRef]
    hidden_columns: dict[TableRef, set[str]] = field(default_factory=dict)
    # {"schema.table" or "table": "SQL condition with :placeholders"}
    row_filters: dict[str, str] = field(default_factory=dict)

    def resolve(self, name: str, schema: str | None) -> TableRef | None:
        name, schema = name.lower(), schema.lower() if schema else None
        if schema:
            ref = TableRef(schema, name)
            return ref if ref in self.tables else None
        matches = [t for t in self.tables if t.table == name]
        return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    tables: frozenset[str]
    limit: int


def validate_sql(
    sql: str,
    dialect: str,
    policy: SchemaPolicy,
    max_limit: int = 500,
    user_context: dict[str, Any] | None = None,
) -> ValidatedQuery:
    read = DIALECTS[dialect]
    try:
        statements = [s for s in sqlglot.parse(sql, read=read) if s is not None]
    except SqlglotError as e:
        raise SQLValidationError(f"Could not parse the SQL: {str(e).splitlines()[0]}") from None
    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL statement is allowed.")
    root = statements[0]
    if not isinstance(root, (exp.Select, exp.SetOperation)):
        raise SQLValidationError("Only SELECT queries are allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    alias_map: dict[str, TableRef] = {}
    used: set[TableRef] = set()

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SQLValidationError(f"{node.key.upper()} is not allowed; only reading data is.")
        if isinstance(node, exp.Func):
            _check_function(node)
        if isinstance(node, exp.Table):
            ref = _check_table(node, policy, cte_names)
            if ref is not None:
                used.add(ref)
                alias_map[(node.alias or node.name).lower()] = ref

    _check_hidden_columns(root, policy, used, alias_map)
    limit = _enforce_limit(root, max_limit)
    _apply_row_filters(root, read, policy, cte_names, user_context or {})

    return ValidatedQuery(
        sql=root.sql(dialect=read, comments=False),
        tables=frozenset(t.qualified for t in used),
        limit=limit,
    )


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def _check_function(node: exp.Func) -> None:
    name = _function_name(node)
    if name in _FORBIDDEN_FUNCTIONS or name.startswith(_FORBIDDEN_PREFIXES):
        raise SQLValidationError(f"The function {name}() is not allowed.")


def _check_table(node: exp.Table, policy: SchemaPolicy, cte_names: set[str]) -> TableRef | None:
    if not isinstance(node.this, exp.Identifier):
        raise SQLValidationError("Table functions are not allowed in FROM; query tables only.")
    if node.catalog:
        raise SQLValidationError("Cross-database references are not allowed.")
    name, schema = node.name, node.db or None
    if schema is None and name.lower() in cte_names:
        return None
    ref = policy.resolve(name, schema)
    if ref is None:
        shown = f"{schema}.{name}" if schema else name
        raise SQLValidationError(f"Table '{shown}' is not available to this connection.")
    return ref


def _check_hidden_columns(
    root: exp.Expression,
    policy: SchemaPolicy,
    used: set[TableRef],
    alias_map: dict[str, TableRef],
) -> None:
    hidden_in_query = {t: policy.hidden_columns.get(t, set()) for t in used}
    if not any(hidden_in_query.values()):
        return

    for star in root.find_all(exp.Star):
        parent = star.parent
        # Only a projected * (SELECT * / t.*) exposes columns; COUNT(*) does not.
        if not isinstance(parent, (exp.Select, exp.Column)):
            continue
        qualifier = parent.table.lower() if isinstance(parent, exp.Column) and parent.table else ""
        targets = [alias_map[qualifier]] if qualifier in alias_map else list(used)
        if any(hidden_in_query.get(t) for t in targets):
            raise SQLValidationError(
                "SELECT * is not allowed on tables with hidden columns; name the columns."
            )

    for col in root.find_all(exp.Column):
        name = col.name.lower()
        qualifier = col.table.lower() if col.table else ""
        if qualifier in alias_map:
            if name in hidden_in_query.get(alias_map[qualifier], set()):
                raise SQLValidationError(f"Column '{col.name}' is hidden and cannot be queried.")
        elif any(name in cols for cols in hidden_in_query.values()):
            raise SQLValidationError(f"Column '{col.name}' is hidden and cannot be queried.")


def _enforce_limit(root: exp.Expression, max_limit: int) -> int:
    limit = root.args.get("limit")
    current: int | None = None
    if isinstance(limit, exp.Limit) and isinstance(limit.expression, exp.Literal):
        try:
            current = int(limit.expression.name)
        except ValueError:
            current = None
    elif isinstance(limit, exp.Fetch) and isinstance(limit.args.get("count"), exp.Literal):
        current = int(limit.args["count"].name)

    final = current if current is not None and 0 <= current <= max_limit else max_limit
    root.set("limit", exp.Limit(expression=exp.Literal.number(final)))
    return final


def _apply_row_filters(
    root: exp.Expression,
    read: str,
    policy: SchemaPolicy,
    cte_names: set[str],
    user_context: dict[str, Any],
) -> None:
    """Replace each filtered table with a subquery that only exposes the allowed rows."""
    if not policy.row_filters:
        return
    filters: dict[TableRef, str] = {}
    for key, condition in policy.row_filters.items():
        schema, _, table = key.rpartition(".")
        ref = policy.resolve(table, schema or None)
        if ref is None:
            raise SQLValidationError(f"Row filter refers to unknown table '{key}'.")
        filters[ref] = condition

    targets = []
    for node in root.find_all(exp.Table):
        if not node.db and node.name.lower() in cte_names:
            continue
        ref = policy.resolve(node.name, node.db or None)
        if ref in filters:
            targets.append((node, ref))

    for node, ref in targets:
        inner = sqlglot.parse_one(
            f"SELECT * FROM {exp.to_identifier(ref.schema).sql(read)}."
            f"{exp.to_identifier(ref.table).sql(read)} WHERE {filters[ref]}",
            read=read,
        )
        _bind_placeholders(inner, user_context)
        node.replace(inner.subquery(node.alias or ref.table))


def _bind_placeholders(tree: exp.Expression, user_context: dict[str, Any]) -> None:
    for ph in list(tree.find_all(exp.Placeholder)):
        key = ph.name
        if not key or key not in user_context:
            # Fail closed: without the user's context we cannot know which rows they may see.
            raise SQLValidationError(f"Missing user context value '{key}' for a row filter.")
        value = user_context[key]
        if isinstance(value, bool) or value is None:
            raise SQLValidationError(f"User context value '{key}' must be text or a number.")
        literal = (
            exp.Literal.number(value)
            if isinstance(value, (int, float))
            else exp.Literal.string(str(value))
        )
        ph.replace(literal)
