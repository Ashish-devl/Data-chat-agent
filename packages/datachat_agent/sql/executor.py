"""Layer 3 of the safety model: run validated SQL in a read-only transaction with limits."""

import base64
import datetime as dt
import json
import math
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError


class QueryExecutionError(Exception):
    def __init__(self, message: str, timed_out: bool = False):
        super().__init__(message)
        self.timed_out = timed_out


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int


def execute_readonly(
    engine: Engine,
    dialect: str,
    sql: str,
    timeout_seconds: float = 10,
    max_rows: int = 500,
    max_bytes: int = 5_000_000,
) -> QueryResult:
    timeout_ms = max(1, int(timeout_seconds * 1000))
    started = time.perf_counter()
    with engine.connect() as conn:
        try:
            if dialect == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql(f"SET LOCAL statement_timeout = {timeout_ms}")
            else:
                conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {timeout_ms}")
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            # Both drivers use %-style placeholders, so literal '%' (LIKE, DATE_FORMAT) is doubled.
            result = conn.exec_driver_sql(sql.replace("%", "%%"))
            columns = list(result.keys())
            rows: list[dict[str, Any]] = []
            size = 0
            truncated = False
            for raw in result:
                if len(rows) >= max_rows:
                    truncated = True
                    break
                row = {c: to_jsonable(v) for c, v in zip(columns, raw, strict=True)}
                size += len(json.dumps(row, default=str))
                if size > max_bytes:
                    truncated = True
                    break
                rows.append(row)
        except DBAPIError as e:
            raise _translate(e, dialect) from None
        finally:
            conn.rollback()
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def _translate(e: DBAPIError, dialect: str) -> QueryExecutionError:
    orig = e.orig
    message = str(orig).strip().splitlines()[0] if orig is not None else str(e)
    sqlstate = getattr(orig, "sqlstate", None)  # psycopg
    args = getattr(orig, "args", None) or (None,)
    timed_out = sqlstate == "57014" or args[0] == 3024  # query_canceled / max execution time
    if timed_out:
        message = "The query took too long and was stopped. Try a narrower question."
    return QueryExecutionError(message, timed_out=timed_out)


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        return f"<{len(data)} bytes>" if len(data) > 64 else base64.b64encode(data).decode()
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    return str(value)
