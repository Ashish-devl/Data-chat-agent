import uuid

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from datachat_agent.core.embeddings import get_embedder
from datachat_agent.sql.connector import get_engine
from datachat_agent.sql.executor import QueryExecutionError, execute_readonly
from datachat_agent.sql.validator import SchemaPolicy, TableRef, validate_sql
from tests.integration.conftest import (
    MY_READONLY,
    PG_READONLY,
    PG_WRITER,
    scanned_connection,
)


def run_pipeline(client, connection_id: str, question: str, responses: list[str], **kw):
    from datachat_agent.server.db import get_sessionmaker
    from datachat_agent.server.models import Connection
    from datachat_agent.sql.pipeline import answer_with_sql

    async def _run():
        async with get_sessionmaker()() as session:
            conn = await session.get(Connection, uuid.UUID(connection_id))
            llm = FakeListChatModel(responses=responses)
            return await answer_with_sql(session, conn, question, llm, get_embedder(), **kw)

    return client.portal.call(_run)


def sql_block(sql: str) -> str:
    return f"```sql\n{sql}\n```"


def test_question_to_rows(client, admin):
    conn = scanned_connection(client, admin, PG_READONLY)
    ans = run_pipeline(
        client,
        conn["id"],
        "How many buyers are there?",
        [sql_block("SELECT COUNT(*) AS buyers FROM public.buyers;")],
    )
    assert ans.error is None and ans.attempts == 1
    assert ans.result.rows == [{"buyers": 6}]
    assert ans.sql.endswith("LIMIT 500")
    assert len(ans.tables_considered) == 8  # small schema: every table is sent


def test_repair_after_hidden_column_refusal(client, admin):
    conn = scanned_connection(client, admin, PG_READONLY)
    schema = {
        t["name"]: t
        for t in client.get(f"/v1/connections/{conn['id']}/schema", headers=admin).json()
    }
    salary = {c["name"]: c for c in schema["employees"]["columns"]}["salary_inr"]
    client.patch(f"/v1/schema-items/{salary['id']}", headers=admin, json={"hidden": True})

    ans = run_pipeline(
        client,
        conn["id"],
        "Average salary by department",
        [
            sql_block("SELECT department, AVG(salary_inr) FROM employees GROUP BY department"),
            sql_block("SELECT department, COUNT(*) AS people FROM employees GROUP BY department"),
        ],
    )
    assert ans.attempts == 2 and ans.error is None
    assert "salary" not in ans.sql
    # The hidden column is not even shown to the model.
    assert all("salary" not in t for t in ans.tables_considered)


def test_db_error_is_repaired_once_then_reported(client, admin):
    conn = scanned_connection(client, admin, PG_READONLY)
    ans = run_pipeline(
        client,
        conn["id"],
        "Total PO value",
        [
            sql_block("SELECT SUM(amountt) FROM purchase_order_lines"),
            sql_block("SELECT SUM(amounttt) FROM purchase_order_lines"),
        ],
    )
    assert ans.attempts == 2 and ans.result is None
    assert "amounttt" in ans.error


def test_model_says_it_cannot_answer(client, admin):
    conn = scanned_connection(client, admin, PG_READONLY)
    ans = run_pipeline(
        client,
        conn["id"],
        "What is the weather in Delhi?",
        ["NO_SQL: The database has no weather data."],
    )
    assert ans.sql is None and ans.cannot_answer == "The database has no weather data."


def test_write_attempt_by_model_never_runs(client, admin):
    conn = scanned_connection(client, admin, PG_READONLY)
    ans = run_pipeline(
        client,
        conn["id"],
        "Delete all buyers",
        [sql_block("DELETE FROM buyers"), sql_block("DROP TABLE buyers")],
    )
    assert ans.result is None and "Only SELECT" in ans.error
    rows = execute_readonly(get_engine(PG_READONLY), "postgresql", "SELECT COUNT(*) n FROM buyers")
    assert rows.rows == [{"n": 6}]


def test_row_filter_limits_rows_to_the_user(client, admin):
    conn = scanned_connection(
        client, admin, PG_READONLY, row_filters={"styles": "buyer_id = :buyer"}
    )
    sql = sql_block("SELECT DISTINCT buyer_id FROM styles")
    ans = run_pipeline(client, conn["id"], "Which buyers?", [sql], user_context={"buyer": 2})
    assert ans.result.rows == [{"buyer_id": 2}]

    ans = run_pipeline(client, conn["id"], "Which buyers?", [sql, sql])  # no user context
    assert ans.result is None and "buyer" in ans.error


def test_retrieval_picks_related_tables_and_fk_neighbours(client, admin):
    from datachat_agent.server.db import get_sessionmaker
    from datachat_agent.server.models import Connection
    from datachat_agent.sql.schema_store import retrieve_schema

    conn = scanned_connection(client, admin, PG_READONLY)

    async def _run():
        async with get_sessionmaker()() as session:
            c = await session.get(Connection, uuid.UUID(conn["id"]))
            vec = get_embedder().embed_query("supplier city")
            return await retrieve_schema(session, c, vec, min_tables=1, max_tables=3)

    tables = [t.name for t in client.portal.call(_run)]
    assert tables[0] == "suppliers"
    assert len(tables) == 3
    # Every extra table is joined to suppliers by a foreign key.
    assert set(tables[1:]) <= {"materials", "purchase_orders"}


def test_mysql_question_to_rows(client, admin, mysql_available):
    conn = scanned_connection(client, admin, MY_READONLY)
    ans = run_pipeline(
        client,
        conn["id"],
        "Delayed production orders per month",
        [
            sql_block(
                "SELECT DATE_FORMAT(due_date, '%Y-%m') AS month, COUNT(*) AS n "
                "FROM production_orders WHERE status LIKE '%Delay%' GROUP BY month ORDER BY month"
            )
        ],
    )
    assert ans.error is None, ans.error
    assert ans.result.rows and all(r["month"].startswith("2026-") for r in ans.result.rows)


# --- Executor guarantees (checklist E6) ---------------------------------------------------


def test_executor_timeout_fires(databases):
    with pytest.raises(QueryExecutionError) as e:
        execute_readonly(
            get_engine(PG_READONLY), "postgresql", "SELECT pg_sleep(3)", timeout_seconds=0.5
        )
    assert e.value.timed_out


def test_executor_blocks_writes_even_for_a_writer_user(databases):
    with pytest.raises(QueryExecutionError, match="read-only"):
        execute_readonly(get_engine(PG_WRITER), "postgresql", "DELETE FROM buyers")


def test_limit_applied_end_to_end(databases):
    policy = SchemaPolicy(tables={TableRef("public", "purchase_order_lines")})
    q = validate_sql("SELECT * FROM purchase_order_lines", "postgresql", policy, max_limit=7)
    assert execute_readonly(get_engine(PG_READONLY), "postgresql", q.sql).row_count == 7
