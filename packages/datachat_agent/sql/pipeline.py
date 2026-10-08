"""Question -> relevant schema -> SQL -> validate -> run read-only.

The agent (week 3) builds on this.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from datachat_agent.config import get_settings
from datachat_agent.core.embeddings import Embedder
from datachat_agent.security import decrypt
from datachat_agent.server.models import Connection
from datachat_agent.sql.connector import get_engine
from datachat_agent.sql.executor import QueryExecutionError, QueryResult, execute_readonly
from datachat_agent.sql.generator import Example, SQLRequest, generate_sql
from datachat_agent.sql.schema_store import build_policy, render_schema, retrieve_schema
from datachat_agent.sql.validator import SQLValidationError, validate_sql


@dataclass
class SQLAnswer:
    question: str
    sql: str | None = None
    result: QueryResult | None = None
    error: str | None = None
    cannot_answer: str | None = None
    tables_considered: list[str] = field(default_factory=list)
    attempts: int = 0
    tokens: int = 0
    latency_ms: int = 0


async def answer_with_sql(
    session: AsyncSession,
    connection: Connection,
    question: str,
    llm: BaseChatModel,
    embedder: Embedder,
    user_context: dict[str, Any] | None = None,
    business_terms: list[str] | None = None,
    examples: list[Example] | None = None,
    history: list[tuple[str, str]] | None = None,
    max_attempts: int = 2,
) -> SQLAnswer:
    started = time.perf_counter()
    settings = get_settings()
    answer = SQLAnswer(question=question)

    vector = await run_in_threadpool(embedder.embed_query, question)
    tables = await retrieve_schema(session, connection, vector)
    answer.tables_considered = [t.qualified for t in tables]
    policy = await build_policy(session, connection)
    engine = get_engine(decrypt(connection.dsn_encrypted))

    req = SQLRequest(
        question=question,
        schema_text=render_schema(tables),
        dialect=connection.dialect,
        business_terms=business_terms or [],
        examples=examples or [],
        history=history or [],
    )
    for attempt in range(1, max_attempts + 1):
        answer.attempts = attempt
        generated = await generate_sql(llm, req)
        answer.tokens += generated.tokens
        if generated.sql is None:
            answer.cannot_answer = generated.reason or "The data does not cover this question."
            break
        answer.sql = generated.sql
        try:
            validated = validate_sql(
                generated.sql,
                connection.dialect,
                policy,
                max_limit=settings.sql_max_rows,
                user_context=user_context,
            )
            answer.sql = validated.sql
            answer.result = await run_in_threadpool(
                execute_readonly,
                engine,
                connection.dialect,
                validated.sql,
                settings.sql_timeout_seconds,
                settings.sql_max_rows,
                settings.sql_max_response_bytes,
            )
            answer.error = None
            break
        except (SQLValidationError, QueryExecutionError) as e:
            answer.error = str(e)
            if isinstance(e, QueryExecutionError) and e.timed_out:
                break  # a retry would just time out again
            req.previous_sql, req.previous_error = generated.sql, str(e)

    answer.latency_ms = int((time.perf_counter() - started) * 1000)
    return answer
