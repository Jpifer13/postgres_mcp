"""Query execution with safety guardrails."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from postgres_mcp.models import AccessMode
from postgres_mcp.sql_utils import validate_read_query, validate_write_query


def _default_serializer(obj: Any) -> Any:
    """JSON serializer for types asyncpg returns that aren't JSON-native."""
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.hex()
    if isinstance(obj, memoryview):
        return obj.tobytes().hex()
    raise TypeError(f"Cannot serialize {type(obj).__name__}")


def _records_to_dicts(records: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [dict(r) for r in records]


def _format_results(records: list[asyncpg.Record], max_rows: int) -> str:
    """Convert query results to a formatted string."""
    if not records:
        return "Query returned 0 rows."

    rows = _records_to_dicts(records[:max_rows])
    truncated = len(records) > max_rows

    result = json.dumps(rows, default=_default_serializer, indent=2)

    # Cap total response size at 1MB
    max_size = 1_000_000
    if len(result) > max_size:
        result = result[:max_size] + "\n... (output truncated due to size)"

    header = f"Rows: {len(rows)}"
    if truncated:
        header += f" (limited to {max_rows}; more rows available)"
    return f"{header}\n{result}"


class QueryExecutor:
    """Runs queries against a connection pool with safety checks."""

    def __init__(self, row_limit: int | None = None) -> None:
        self.row_limit = row_limit or int(os.getenv("PG_MCP_ROW_LIMIT", "500"))

    async def read_query(
        self,
        pool: asyncpg.Pool,
        sql: str,
        params: list[Any] | None = None,
    ) -> str:
        validate_read_query(sql)
        async with pool.acquire() as conn:
            records = await conn.fetch(sql, *(params or []))
        return _format_results(records, self.row_limit)

    async def write_query(
        self,
        pool: asyncpg.Pool,
        mode: AccessMode,
        sql: str,
        params: list[Any] | None = None,
        allow_ddl: bool = False,
    ) -> str:
        if mode != AccessMode.READWRITE:
            raise ValueError(
                "Write operations require a 'readwrite' connection. "
                "Reconnect with mode='readwrite' to enable writes."
            )
        validate_write_query(sql, allow_ddl=allow_ddl)

        async with pool.acquire() as conn:
            async with conn.transaction():
                result = await conn.execute(sql, *(params or []))

        return f"Executed successfully. {result}"

    async def explain_safe(
        self,
        pool: asyncpg.Pool,
        sql: str,
        analyze: bool = False,
    ) -> str:
        """EXPLAIN with rollback for ANALYZE to prevent side effects."""
        if analyze:
            explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"
        else:
            explain_sql = f"EXPLAIN (FORMAT JSON) {sql}"

        async with pool.acquire() as conn:
            if analyze:
                # Use a savepoint so we can rollback the effects of ANALYZE
                tr = conn.transaction()
                await tr.start()
                try:
                    records = await conn.fetch(explain_sql)
                finally:
                    await tr.rollback()
            else:
                records = await conn.fetch(explain_sql)

        plan = [dict(r) for r in records]
        return json.dumps(plan, default=_default_serializer, indent=2)

    async def sample_data(
        self,
        pool: asyncpg.Pool,
        table: str,
        schema: str = "public",
        limit: int = 5,
    ) -> str:
        """Return a small sample of rows from a table."""
        # Use qualified identifier to prevent SQL injection
        sql = f"SELECT * FROM {_quote_ident(schema)}.{_quote_ident(table)} LIMIT $1"
        async with pool.acquire() as conn:
            records = await conn.fetch(sql, limit)
        return _format_results(records, limit)


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier to prevent injection."""
    # Double any existing double-quotes, then wrap in double-quotes
    return '"' + name.replace('"', '""') + '"'
