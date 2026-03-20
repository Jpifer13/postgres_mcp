"""PostgreSQL MCP Server — multi-connection, schema-aware database management."""

from __future__ import annotations

import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from postgres_mcp import pg_queries as Q
from postgres_mcp.connection_manager import ConnectionManager
from postgres_mcp.query_executor import QueryExecutor, _quote_ident

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("postgres_mcp")

conn_mgr = ConnectionManager()
executor = QueryExecutor()


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Load env-based connections on startup, close all on shutdown."""
    await conn_mgr.load_from_env()
    try:
        yield {}
    finally:
        await conn_mgr.close_all()


mcp = FastMCP("postgres-mcp", lifespan=lifespan)


# ======================================================================
# Connection Management Tools
# ======================================================================

@mcp.tool()
async def connect(alias: str, dsn: str, mode: str = "readonly") -> str:
    """Register a new PostgreSQL database connection.

    Args:
        alias: Short name for this connection (e.g. "prod", "staging", "local")
        dsn: PostgreSQL connection string (postgresql://user:pass@host:port/dbname)
        mode: "readonly" (default, safe) or "readwrite" (enables INSERT/UPDATE/DELETE)
    """
    return await conn_mgr.connect(alias, dsn, mode)


@mcp.tool()
async def disconnect(alias: str) -> str:
    """Close and remove a database connection.

    Args:
        alias: Name of the connection to disconnect
    """
    return await conn_mgr.disconnect(alias)


@mcp.tool()
async def list_connections() -> str:
    """List all active database connections with their status (credentials are never shown)."""
    conns = await conn_mgr.list_connections()
    if not conns:
        return "No active connections. Use 'connect' to add one."
    return json.dumps([c.model_dump() for c in conns], indent=2)


# ======================================================================
# Schema Introspection Tools
# ======================================================================

async def _fetch_json(alias: str, sql: str, params: list[Any] | None = None) -> str:
    """Helper: run a catalog query and return JSON."""
    pool = await conn_mgr.get_pool(alias)
    async with pool.acquire() as conn:
        records = await conn.fetch(sql, *(params or []))
    rows = [dict(r) for r in records]
    if not rows:
        return "No results."
    return json.dumps(rows, indent=2, default=str)


@mcp.tool()
async def list_schemas(connection: str) -> str:
    """List all user-defined schemas with table and view counts.

    Args:
        connection: Connection alias to use
    """
    return await _fetch_json(connection, Q.LIST_SCHEMAS)


@mcp.tool()
async def list_tables(connection: str, schema: str = "public") -> str:
    """List tables and views in a schema with row estimates and sizes.

    Args:
        connection: Connection alias to use
        schema: Schema name (default: "public")
    """
    return await _fetch_json(connection, Q.LIST_TABLES, [schema])


@mcp.tool()
async def describe_table(connection: str, table: str, schema: str = "public") -> str:
    """Get full table details: columns, primary key, foreign keys, indexes, constraints, and triggers.

    Args:
        connection: Connection alias to use
        table: Table name
        schema: Schema name (default: "public")
    """
    qualified = f"{_quote_ident(schema)}.{_quote_ident(table)}"
    pool = await conn_mgr.get_pool(connection)

    sections: dict[str, Any] = {}
    async with pool.acquire() as conn:
        # Columns
        records = await conn.fetch(Q.DESCRIBE_COLUMNS, qualified)
        sections["columns"] = [dict(r) for r in records]

        # Primary key
        records = await conn.fetch(Q.DESCRIBE_PRIMARY_KEY, qualified)
        sections["primary_key"] = [dict(r) for r in records]

        # Foreign keys
        records = await conn.fetch(Q.DESCRIBE_FOREIGN_KEYS, qualified)
        sections["foreign_keys"] = [dict(r) for r in records]

        # Indexes
        records = await conn.fetch(Q.DESCRIBE_INDEXES, qualified)
        sections["indexes"] = [dict(r) for r in records]

        # All constraints
        records = await conn.fetch(Q.DESCRIBE_CONSTRAINTS, qualified)
        sections["constraints"] = [dict(r) for r in records]

        # Triggers
        records = await conn.fetch(Q.DESCRIBE_TRIGGERS, qualified)
        sections["triggers"] = [dict(r) for r in records]

    return json.dumps(sections, indent=2, default=str)


@mcp.tool()
async def list_indexes(
    connection: str, schema: str = "public", table: str | None = None
) -> str:
    """List indexes in a schema, optionally filtered to a specific table.

    Args:
        connection: Connection alias to use
        schema: Schema name (default: "public")
        table: Optional table name to filter indexes
    """
    if table:
        return await _fetch_json(connection, Q.LIST_INDEXES_TABLE, [schema, table])
    return await _fetch_json(connection, Q.LIST_INDEXES_SCHEMA, [schema])


@mcp.tool()
async def list_constraints(
    connection: str, schema: str = "public", table: str | None = None
) -> str:
    """List constraints (PK, FK, unique, check, exclusion) in a schema or table.

    Args:
        connection: Connection alias to use
        schema: Schema name (default: "public")
        table: Optional table name to filter constraints
    """
    if table:
        return await _fetch_json(connection, Q.LIST_CONSTRAINTS_TABLE, [schema, table])
    return await _fetch_json(connection, Q.LIST_CONSTRAINTS_SCHEMA, [schema])


@mcp.tool()
async def list_functions(connection: str, schema: str = "public") -> str:
    """List functions and procedures with their signatures.

    Args:
        connection: Connection alias to use
        schema: Schema name (default: "public")
    """
    return await _fetch_json(connection, Q.LIST_FUNCTIONS, [schema])


@mcp.tool()
async def list_enums(connection: str, schema: str = "public") -> str:
    """List enum types and their values.

    Args:
        connection: Connection alias to use
        schema: Schema name (default: "public")
    """
    return await _fetch_json(connection, Q.LIST_ENUMS, [schema])


@mcp.tool()
async def get_table_stats(connection: str, table: str, schema: str = "public") -> str:
    """Get table statistics: row counts, dead tuples, vacuum info, scan counts.

    Args:
        connection: Connection alias to use
        table: Table name
        schema: Schema name (default: "public")
    """
    return await _fetch_json(connection, Q.TABLE_STATS_SIMPLE, [schema, table])


# ======================================================================
# Query Execution Tools
# ======================================================================

@mcp.tool()
async def query(connection: str, sql: str, params: list[Any] | None = None) -> str:
    """Execute a read-only SQL query (SELECT, WITH, SHOW, etc.) and return results.

    Results are returned as JSON with a configurable row limit (default 500).

    Args:
        connection: Connection alias to use
        sql: SQL query to execute (must be a SELECT/WITH/SHOW statement)
        params: Optional list of query parameters for $1, $2, etc. placeholders
    """
    pool = await conn_mgr.get_pool(connection)
    return await executor.read_query(pool, sql, params)


@mcp.tool()
async def execute(
    connection: str,
    sql: str,
    params: list[Any] | None = None,
    allow_ddl: bool = False,
) -> str:
    """Execute a write SQL statement (INSERT, UPDATE, DELETE) on a readwrite connection.

    Wrapped in a transaction. DDL (CREATE, DROP, ALTER) is blocked by default.

    Args:
        connection: Connection alias to use
        sql: SQL statement to execute
        params: Optional list of query parameters for $1, $2, etc. placeholders
        allow_ddl: Set True to allow DDL statements (dangerous)
    """
    pool = await conn_mgr.get_pool(connection)
    mode = conn_mgr.get_mode(connection)
    return await executor.write_query(pool, mode, sql, params, allow_ddl)


@mcp.tool()
async def explain_query(
    connection: str, sql: str, analyze: bool = False
) -> str:
    """Show the query execution plan (EXPLAIN). Use analyze=True to run the query and show actual timings.

    EXPLAIN ANALYZE is rolled back to prevent side effects.

    Args:
        connection: Connection alias to use
        sql: SQL query to explain
        analyze: If True, actually execute and show real timings (rolled back)
    """
    pool = await conn_mgr.get_pool(connection)
    return await executor.explain_safe(pool, sql, analyze)


# ======================================================================
# Utility Tools
# ======================================================================

@mcp.tool()
async def get_database_info(connection: str) -> str:
    """Get database metadata: PostgreSQL version, size, encoding, extensions, active connections.

    Args:
        connection: Connection alias to use
    """
    pool = await conn_mgr.get_pool(connection)
    async with pool.acquire() as conn:
        db_info = await conn.fetchrow(Q.DATABASE_INFO)
        extensions = await conn.fetch(Q.INSTALLED_EXTENSIONS)

    result = dict(db_info) if db_info else {}
    result["extensions"] = [dict(r) for r in extensions]
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def get_slow_queries(connection: str, limit: int = 10) -> str:
    """Show top N slowest queries from pg_stat_statements (requires the extension).

    Args:
        connection: Connection alias to use
        limit: Number of queries to return (default: 10)
    """
    try:
        return await _fetch_json(connection, Q.SLOW_QUERIES, [limit])
    except Exception as e:
        if "pg_stat_statements" in str(e):
            return (
                "pg_stat_statements extension is not installed or enabled. "
                "Run: CREATE EXTENSION pg_stat_statements;"
            )
        raise


@mcp.tool()
async def get_active_queries(connection: str) -> str:
    """Show currently running queries with PID, duration, state, and query text.

    Args:
        connection: Connection alias to use
    """
    return await _fetch_json(connection, Q.ACTIVE_QUERIES)


@mcp.tool()
async def get_locks(connection: str) -> str:
    """Show current lock information — useful for debugging blocked queries.

    Args:
        connection: Connection alias to use
    """
    return await _fetch_json(connection, Q.LOCKS)


@mcp.tool()
async def sample_table_data(
    connection: str, table: str, schema: str = "public", limit: int = 5
) -> str:
    """Return a small sample of rows from a table to understand data shape and content.

    Args:
        connection: Connection alias to use
        table: Table name
        schema: Schema name (default: "public")
        limit: Number of sample rows (default: 5)
    """
    pool = await conn_mgr.get_pool(connection)
    return await executor.sample_data(pool, table, schema, min(limit, 100))


# ======================================================================
# Entry point
# ======================================================================

def main():
    mcp.run()


if __name__ == "__main__":
    main()
