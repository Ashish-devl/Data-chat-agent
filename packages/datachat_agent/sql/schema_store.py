"""Save scanned schemas as schema_items, and find the few tables a question needs."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from datachat_agent.core.embeddings import Embedder
from datachat_agent.server.models import Connection, SchemaItem
from datachat_agent.sql.schema_scanner import TableInfo
from datachat_agent.sql.validator import SchemaPolicy, TableRef

MIN_TABLES = 5
MAX_TABLES = 8


def embedding_text(item: SchemaItem) -> str:
    """What gets embedded for retrieval. Admin descriptions come first so they weigh most."""
    words = lambda s: s.replace("_", " ")  # noqa: E731 - "po_date" should match "PO date"
    if item.column_name is None:
        parts = [f"table {words(item.table_name)}"]
        if item.description:
            parts.insert(0, item.description)
        return ". ".join(parts)
    parts = [f"{words(item.table_name)} {words(item.column_name)} ({item.data_type})"]
    if item.description:
        parts.insert(0, item.description)
    if item.sample_values:
        parts.append("examples: " + ", ".join(map(str, item.sample_values)))
    return ". ".join(parts)


async def save_scan(
    session: AsyncSession, connection: Connection, tables: list[TableInfo], embedder: Embedder
) -> dict[str, int]:
    """Upsert scanned items, keeping admin descriptions and hidden flags; drop vanished ones."""
    existing = {
        (i.schema_name, i.table_name, i.column_name): i
        for i in await session.scalars(
            select(SchemaItem).where(SchemaItem.connection_id == connection.id)
        )
    }
    seen: set[tuple[str, str, str | None]] = set()
    touched: list[SchemaItem] = []

    def upsert(key: tuple[str, str, str | None], **values) -> SchemaItem:
        item = existing.get(key)
        if item is None:
            item = SchemaItem(
                id=uuid.uuid4(),
                connection_id=connection.id,
                schema_name=key[0],
                table_name=key[1],
                column_name=key[2],
                hidden=False,
                description=None,
            )
            session.add(item)
        for k, v in values.items():
            setattr(item, k, v)
        seen.add(key)
        touched.append(item)
        return item

    for t in tables:
        upsert(
            (t.schema, t.name, None),
            data_type="view" if t.is_view else "table",
            row_count=t.row_count,
            is_primary_key=False,
            foreign_key=None,
            sample_values=[],
        )
        for c in t.columns:
            upsert(
                (t.schema, t.name, c.name),
                data_type=c.data_type,
                is_primary_key=c.is_primary_key,
                foreign_key=c.foreign_key,
                sample_values=c.samples,
                row_count=None,
            )

    removed = 0
    for key, item in existing.items():
        if key not in seen:
            await session.delete(item)
            removed += 1

    vectors = await run_in_threadpool(
        embedder.embed_documents, [embedding_text(i) for i in touched]
    )
    for item, vec in zip(touched, vectors, strict=True):
        item.embedding = vec

    connection.last_scanned_at = datetime.now(UTC)
    await session.commit()
    return {
        "tables": len(tables),
        "columns": sum(len(t.columns) for t in tables),
        "removed": removed,
    }


async def build_policy(session: AsyncSession, connection: Connection) -> SchemaPolicy:
    items = list(
        await session.scalars(select(SchemaItem).where(SchemaItem.connection_id == connection.id))
    )
    hidden_tables = {
        (i.schema_name, i.table_name) for i in items if i.column_name is None and i.hidden
    }
    tables = {
        TableRef(i.schema_name.lower(), i.table_name.lower())
        for i in items
        if i.column_name is None and (i.schema_name, i.table_name) not in hidden_tables
    }
    hidden_columns: dict[TableRef, set[str]] = {}
    for i in items:
        if i.column_name is not None and i.hidden:
            ref = TableRef(i.schema_name.lower(), i.table_name.lower())
            hidden_columns.setdefault(ref, set()).add(i.column_name.lower())
    return SchemaPolicy(
        tables=tables, hidden_columns=hidden_columns, row_filters=dict(connection.row_filters or {})
    )


@dataclass
class CompactColumn:
    name: str
    data_type: str
    is_primary_key: bool
    foreign_key: str | None
    description: str | None
    samples: list[str]


@dataclass
class CompactTable:
    schema: str
    name: str
    description: str | None
    row_count: int | None
    columns: list[CompactColumn] = field(default_factory=list)

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"


async def retrieve_schema(
    session: AsyncSession,
    connection: Connection,
    question_vector: list[float],
    min_tables: int = MIN_TABLES,
    max_tables: int = MAX_TABLES,
) -> list[CompactTable]:
    """Pick the tables most related to the question, then add tables joined to them by FK."""
    items = list(
        await session.scalars(
            select(SchemaItem)
            .where(SchemaItem.connection_id == connection.id)
            .order_by(SchemaItem.schema_name, SchemaItem.table_name, SchemaItem.column_name)
        )
    )
    hidden_tables = {
        (i.schema_name, i.table_name) for i in items if i.column_name is None and i.hidden
    }
    visible = [
        i for i in items if not i.hidden and (i.schema_name, i.table_name) not in hidden_tables
    ]
    all_tables = [(i.schema_name, i.table_name) for i in visible if i.column_name is None]

    if len(all_tables) <= max_tables:
        chosen = all_tables
    else:
        distance = SchemaItem.embedding.cosine_distance(question_vector)
        ranked = await session.execute(
            select(SchemaItem.schema_name, SchemaItem.table_name, distance.label("d"))
            .where(
                SchemaItem.connection_id == connection.id,
                SchemaItem.hidden.is_(False),
                SchemaItem.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(200)
        )
        chosen = []
        for schema, table, _ in ranked:
            key = (schema, table)
            if key in hidden_tables or key in chosen:
                continue
            chosen.append(key)
            if len(chosen) >= min_tables:
                break
        chosen = _expand_by_foreign_keys(chosen, visible, max_tables)

    by_table: dict[tuple[str, str], CompactTable] = {}
    for i in visible:
        key = (i.schema_name, i.table_name)
        if key not in chosen:
            continue
        if i.column_name is None:
            by_table[key] = CompactTable(i.schema_name, i.table_name, i.description, i.row_count)
    for i in visible:
        key = (i.schema_name, i.table_name)
        if i.column_name is not None and key in by_table:
            by_table[key].columns.append(
                CompactColumn(
                    i.column_name,
                    i.data_type or "",
                    i.is_primary_key,
                    i.foreign_key,
                    i.description,
                    list(i.sample_values or []),
                )
            )
    return [by_table[k] for k in chosen if k in by_table]


def _expand_by_foreign_keys(
    chosen: list[tuple[str, str]], items: list[SchemaItem], max_tables: int
) -> list[tuple[str, str]]:
    links: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for i in items:
        if i.column_name is None or not i.foreign_key:
            continue
        schema, table, _ = i.foreign_key.rsplit(".", 2)
        a, b = (i.schema_name, i.table_name), (schema, table)
        links.setdefault(a, set()).add(b)
        links.setdefault(b, set()).add(a)
    visible_tables = {(i.schema_name, i.table_name) for i in items if i.column_name is None}
    result = list(chosen)
    for table in chosen:
        for neighbour in sorted(links.get(table, ())):
            if len(result) >= max_tables:
                return result
            if neighbour not in result and neighbour in visible_tables:
                result.append(neighbour)
    return result


def render_schema(tables: list[CompactTable]) -> str:
    """The compact schema text the SQL model sees."""
    blocks = []
    for t in tables:
        head = f"TABLE {t.qualified}"
        if t.row_count is not None:
            head += f"  (~{t.row_count:,} rows)"
        if t.description:
            head += f"  -- {t.description}"
        lines = [head]
        for c in t.columns:
            line = f"  {c.name} {c.data_type}"
            if c.is_primary_key:
                line += " PRIMARY KEY"
            if c.foreign_key:
                line += f" REFERENCES {c.foreign_key}"
            notes = []
            if c.description:
                notes.append(c.description)
            if c.samples:
                notes.append("e.g. " + ", ".join(repr(s) for s in c.samples))
            if notes:
                line += "  -- " + "; ".join(notes)
            lines.append(line)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
