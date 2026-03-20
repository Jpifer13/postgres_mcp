# postgres-mcp

Multi-connection PostgreSQL MCP server for Claude Code.

## Features

- **Multi-connection management** — connect to multiple databases simultaneously with named aliases
- **Schema introspection** — explore schemas, tables, columns, indexes, constraints, functions, enums
- **Safe query execution** — read-only by default, opt-in write mode with guardrails
- **Database diagnostics** — active queries, locks, slow queries, table stats
- **Credential safety** — connection strings are never echoed back in responses

## Installation

```bash
poetry install
```

## Usage with Claude Code

Add to your Claude Code MCP settings (`~/.claude/settings.json` or project `.claude/settings.json`):

```json
{
  "mcpServers": {
    "postgres": {
      "command": "poetry",
      "args": ["run", "--directory", "/path/to/postgres_mcp", "postgres-mcp"]
    }
  }
}
```

Or pre-configure connections via environment variables:

```json
{
  "mcpServers": {
    "postgres": {
      "command": "poetry",
      "args": ["run", "--directory", "/path/to/postgres_mcp", "postgres-mcp"],
      "env": {
        "PG_MCP_CONN_local": "postgresql://user:pass@localhost:5432/mydb",
        "PG_MCP_CONN_staging": "postgresql://user:pass@staging-host:5432/app"
      }
    }
  }
}
```

## Tools

### Connection Management
- `connect` — register a new database connection
- `disconnect` — close a connection
- `list_connections` — show all active connections

### Schema Introspection
- `list_schemas` — list schemas with table/view counts
- `list_tables` — list tables/views with row estimates and sizes
- `describe_table` — full table details (columns, keys, indexes, triggers)
- `list_indexes` — indexes with usage stats
- `list_constraints` — constraints with definitions
- `list_functions` — functions/procedures
- `list_enums` — enum types and values
- `get_table_stats` — vacuum info, scan counts, dead tuples

### Query Execution
- `query` — execute SELECT queries (read-only, row-limited)
- `execute` — execute INSERT/UPDATE/DELETE (requires readwrite connection)
- `explain_query` — show query execution plans

### Diagnostics
- `get_database_info` — version, size, encoding, extensions
- `get_slow_queries` — top N slowest queries (requires pg_stat_statements)
- `get_active_queries` — currently running queries
- `get_locks` — current lock information
- `sample_table_data` — quick sample of rows from a table

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PG_MCP_CONN_{ALIAS}` | — | Auto-register connection at startup |
| `PG_MCP_CONN_{ALIAS}__MODE` | `readonly` | Access mode for auto-registered connection |
| `PG_MCP_DEFAULT_MODE` | `readonly` | Default mode for new connections |
| `PG_MCP_ROW_LIMIT` | `500` | Max rows returned by query tool |
| `PG_MCP_QUERY_TIMEOUT` | `30` | Query timeout in seconds |
| `PG_MCP_POOL_MIN` | `1` | Min connections per pool |
| `PG_MCP_POOL_MAX` | `5` | Max connections per pool |

## Safety

- Connections are **readonly by default** — PostgreSQL enforces `default_transaction_read_only = ON`
- SQL is validated before execution (defense in depth)
- Multi-statement queries are rejected
- DDL is blocked in `execute` unless `allow_ddl=True`
- Results are capped at 500 rows / 1MB
- Connection credentials are never included in tool outputs
