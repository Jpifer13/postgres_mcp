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

---

## Claude Code Examples

These examples show what it looks like to use the postgres MCP server in a natural conversation with Claude Code.

---

### "Show me all appointments for John Smith"

**You:**
> Connect to my clinic database at localhost and show me all upcoming appointments for the client named John Smith

**Claude Code:**

First I'll connect to your database and explore the schema to find the relevant tables.

```
Tool: connect
  alias: "clinic"
  dsn:   "postgresql://user:pass@localhost:5432/clinic"
  mode:  "readonly"
```
```
Connected: clinic → clinic@localhost:5432 [readonly]
```

```
Tool: list_tables
  connection: "clinic"
  schema:     "public"
```
```json
[
  { "table": "clients",      "type": "table", "rows_estimate": 2340,  "total_size": "1 MB"  },
  { "table": "appointments", "type": "table", "rows_estimate": 18420, "total_size": "4 MB"  },
  { "table": "providers",    "type": "table", "rows_estimate": 45,    "total_size": "32 kB" },
  { "table": "services",     "type": "table", "rows_estimate": 120,   "total_size": "48 kB" }
]
```

```
Tool: describe_table
  connection: "clinic"
  table:      "appointments"
```

Now I can see the schema. Let me query for John Smith's upcoming appointments.

```
Tool: query
  connection: "clinic"
  sql:        "SELECT a.id, a.scheduled_at, a.duration_min, p.name AS provider,
                      s.name AS service, a.status
               FROM appointments a
               JOIN clients c ON c.id = a.client_id
               JOIN providers p ON p.id = a.provider_id
               JOIN services s ON s.id = a.service_id
               WHERE c.first_name = 'John' AND c.last_name = 'Smith'
                 AND a.scheduled_at >= now()
               ORDER BY a.scheduled_at"
```
```json
[
  { "id": 8012, "scheduled_at": "2026-03-23T10:00:00Z", "duration_min": 60, "provider": "Dr. Rivera",  "service": "Follow-up",      "status": "confirmed" },
  { "id": 8241, "scheduled_at": "2026-04-07T14:30:00Z", "duration_min": 30, "provider": "Dr. Chen",    "service": "Lab Review",      "status": "pending"   },
  { "id": 8509, "scheduled_at": "2026-04-21T09:00:00Z", "duration_min": 90, "provider": "Dr. Rivera",  "service": "Annual Physical", "status": "confirmed" }
]
```

John Smith has 3 upcoming appointments:

1. **Mar 23 at 10:00 AM** — 60-min Follow-up with Dr. Rivera (confirmed)
2. **Apr 7 at 2:30 PM** — 30-min Lab Review with Dr. Chen (pending)
3. **Apr 21 at 9:00 AM** — 90-min Annual Physical with Dr. Rivera (confirmed)

---

### "Which tables have the most bloat?"

**You:**
> Check the prod database for tables that need vacuuming

**Claude Code:**

```
Tool: get_table_stats
  connection: "prod"
```
```json
[
  { "table": "order_items", "live_rows": 1843201, "dead_rows": 312400, "last_autovacuum": "2026-03-14T02:15:00Z" },
  { "table": "events",      "live_rows": 920100,  "dead_rows": 184300, "last_autovacuum": "2026-03-12T06:40:00Z" },
  { "table": "sessions",    "live_rows": 54000,   "dead_rows": 1200,   "last_autovacuum": "2026-03-19T11:00:00Z" },
  { "table": "users",       "live_rows": 84201,   "dead_rows": 320,    "last_autovacuum": "2026-03-19T14:30:00Z" }
]
```

Two tables stand out with high dead-row ratios:

- **order_items** — 312k dead rows (17% of live), last vacuumed 6 days ago
- **events** — 184k dead rows (20% of live), last vacuumed 8 days ago

Both would benefit from a manual `VACUUM ANALYZE`. The other tables look healthy.

---

### "Help me understand why this query is slow"

**You:**
> This query takes forever: `SELECT * FROM orders WHERE customer_email = 'jane@example.com'`

**Claude Code:**

Let me check the execution plan and look for missing indexes.

```
Tool: explain_query
  connection: "prod"
  sql:        "SELECT * FROM orders WHERE customer_email = 'jane@example.com'"
  analyze:    true
```
```
Seq Scan on orders  (cost=0.00..18204.43 rows=12 width=64)
                    (actual time=892.10..1204.55 rows=9 loops=1)
  Filter: (customer_email = 'jane@example.com'::text)
  Rows Removed by Filter: 512034
Planning Time: 0.3 ms
Execution Time: 1204.6 ms
```

The problem is clear — PostgreSQL is doing a **sequential scan** across all 512k rows because there's no index on `customer_email`. It's checking every single row and filtering out all but 9.

```
Tool: list_indexes
  connection: "prod"
  table:      "orders"
```
```json
[
  { "name": "orders_pkey",           "columns": ["id"],         "unique": true  },
  { "name": "orders_user_id_idx",    "columns": ["user_id"],    "unique": false },
  { "name": "orders_created_at_idx", "columns": ["created_at"], "unique": false }
]
```

Confirmed — no index on `customer_email`. Adding one should bring this query from ~1.2s down to under 1ms:

```sql
CREATE INDEX CONCURRENTLY orders_customer_email_idx ON orders (customer_email);
```

---

### "Move a client's appointments to a different provider"

**You:**
> Reassign all of John Smith's upcoming appointments from Dr. Rivera to Dr. Patel

**Claude Code:**

Let me first verify the IDs we'll need, then make the update.

```
Tool: query
  connection: "clinic"
  sql:        "SELECT a.id, a.scheduled_at, s.name AS service
               FROM appointments a
               JOIN clients c ON c.id = a.client_id
               JOIN providers p ON p.id = a.provider_id
               WHERE c.first_name = 'John' AND c.last_name = 'Smith'
                 AND p.name = 'Dr. Rivera'
                 AND a.scheduled_at >= now()"
```
```json
[
  { "id": 8012, "scheduled_at": "2026-03-23T10:00:00Z", "service": "Follow-up"      },
  { "id": 8509, "scheduled_at": "2026-04-21T09:00:00Z", "service": "Annual Physical" }
]
```

That's 2 appointments to reassign. I'll need a readwrite connection for this.

```
Tool: connect
  alias: "clinic-rw"
  dsn:   "postgresql://admin:pass@localhost:5432/clinic"
  mode:  "readwrite"
```

```
Tool: execute
  connection: "clinic-rw"
  sql:        "UPDATE appointments
               SET provider_id = (SELECT id FROM providers WHERE name = 'Dr. Patel')
               WHERE id = ANY($1)"
  params:     [[8012, 8509]]
```
```
UPDATE 2
```

Done — both appointments have been reassigned to Dr. Patel:

1. **Mar 23** — Follow-up (was Dr. Rivera, now Dr. Patel)
2. **Apr 21** — Annual Physical (was Dr. Rivera, now Dr. Patel)

---

### "What's going on with the database right now?"

**You:**
> The app feels sluggish, can you check what's happening on prod?

**Claude Code:**

```
Tool: get_active_queries
  connection: "prod"
```
```json
[
  { "pid": 29105, "state": "active",  "duration": "00:00:12.44", "query": "SELECT o.*, oi.* FROM orders o JOIN order_items oi ON oi.order_id = o.id WHERE o.created_at > '2025-01-01'", "wait_event": null },
  { "pid": 29200, "state": "idle in transaction", "duration": "00:04:22.01", "query": "BEGIN", "wait_event": "ClientRead" }
]
```

```
Tool: get_locks
  connection: "prod"
```
```json
[
  { "pid": 29200, "relation": "orders", "lock_type": "RowExclusiveLock", "granted": true,  "blocked_by": null  },
  { "pid": 29105, "relation": "orders", "lock_type": "AccessShareLock",  "granted": true,  "blocked_by": null  }
]
```

Two issues:

1. **PID 29105** — A heavy join across `orders` and `order_items` scanning everything since Jan 2025 has been running for 12 seconds. This is likely the source of the sluggishness.
2. **PID 29200** — An idle transaction has been holding a `RowExclusiveLock` on `orders` for over 4 minutes. The client isn't sending anything (`ClientRead` wait). This looks like an abandoned transaction from the app that should be investigated.

---

## Visual Examples

### Connecting to a database

```
Tool: connect
  alias: "prod"
  dsn:   "postgresql://app_user:••••••@db.example.com:5432/appdb"
  mode:  "readonly"
```
```
Connected: prod → appdb@db.example.com:5432 [readonly]
```

---

### Listing active connections

```
Tool: list_connections
```
```json
[
  {
    "alias": "prod",
    "host": "db.example.com",
    "port": 5432,
    "database": "appdb",
    "user": "app_user",
    "mode": "readonly",
    "pool_size": 3
  },
  {
    "alias": "local",
    "host": "localhost",
    "port": 5432,
    "database": "mydb",
    "user": "jake",
    "mode": "readwrite",
    "pool_size": 1
  }
]
```

---

### Exploring schemas

```
Tool: list_schemas
  connection: "prod"
```
```json
[
  { "schema": "public",  "tables": 14, "views": 3 },
  { "schema": "billing", "tables": 6,  "views": 1 },
  { "schema": "audit",   "tables": 2,  "views": 0 }
]
```

---

### Listing tables

```
Tool: list_tables
  connection: "prod"
  schema:     "public"
```
```json
[
  { "table": "users",        "type": "table", "rows_estimate": 84201,  "total_size": "18 MB" },
  { "table": "orders",       "type": "table", "rows_estimate": 512043, "total_size": "124 MB" },
  { "table": "products",     "type": "table", "rows_estimate": 3892,   "total_size": "2 MB" },
  { "table": "order_items",  "type": "table", "rows_estimate": 1843201, "total_size": "412 MB" },
  { "table": "active_users", "type": "view",  "rows_estimate": null,   "total_size": null }
]
```

---

### Describing a table

```
Tool: describe_table
  connection: "prod"
  table:      "orders"
  schema:     "public"
```
```json
{
  "columns": [
    { "column": "id",          "type": "bigint",                    "nullable": false, "default": "nextval('orders_id_seq')" },
    { "column": "user_id",     "type": "bigint",                    "nullable": false, "default": null },
    { "column": "status",      "type": "order_status",              "nullable": false, "default": "'pending'" },
    { "column": "total_cents", "type": "integer",                   "nullable": false, "default": null },
    { "column": "created_at",  "type": "timestamp with time zone",  "nullable": false, "default": "now()" },
    { "column": "updated_at",  "type": "timestamp with time zone",  "nullable": false, "default": "now()" }
  ],
  "primary_key": [
    { "column": "id" }
  ],
  "foreign_keys": [
    { "column": "user_id", "references_table": "users", "references_column": "id", "on_delete": "RESTRICT" }
  ],
  "indexes": [
    { "name": "orders_pkey",           "columns": ["id"],         "unique": true,  "type": "btree" },
    { "name": "orders_user_id_idx",    "columns": ["user_id"],    "unique": false, "type": "btree" },
    { "name": "orders_created_at_idx", "columns": ["created_at"], "unique": false, "type": "brin"  }
  ],
  "constraints": [
    { "name": "orders_total_cents_check", "type": "CHECK", "definition": "CHECK (total_cents >= 0)" }
  ],
  "triggers": [
    { "name": "set_updated_at", "event": "BEFORE UPDATE", "function": "trigger_set_timestamp()" }
  ]
}
```

---

### Running a query

```
Tool: query
  connection: "prod"
  sql:        "SELECT status, COUNT(*) AS cnt, SUM(total_cents) AS revenue_cents
               FROM orders
               WHERE created_at >= now() - INTERVAL '7 days'
               GROUP BY status
               ORDER BY cnt DESC"
```
```json
[
  { "status": "completed", "cnt": 4821,  "revenue_cents": 9432100 },
  { "status": "pending",   "cnt": 312,   "revenue_cents": 718400  },
  { "status": "cancelled", "cnt": 88,    "revenue_cents": 0        }
]
```

---

### Sampling table data

```
Tool: sample_table_data
  connection: "prod"
  table:      "users"
  schema:     "public"
  limit:      3
```
```json
[
  { "id": 1042, "email": "alice@example.com", "plan": "pro",   "created_at": "2024-01-15T09:22:11Z" },
  { "id": 2891, "email": "bob@example.com",   "plan": "free",  "created_at": "2024-03-02T14:05:33Z" },
  { "id": 5003, "email": "carol@example.com", "plan": "pro",   "created_at": "2024-06-20T08:44:52Z" }
]
```

---

### Explaining a query

```
Tool: explain_query
  connection: "prod"
  sql:        "SELECT * FROM orders WHERE user_id = 42"
  analyze:    false
```
```
Index Scan using orders_user_id_idx on orders  (cost=0.42..18.30 rows=7 width=64)
  Index Cond: (user_id = 42)
```

With `analyze: true` (executes and rolls back):
```
Index Scan using orders_user_id_idx on orders  (cost=0.42..18.30 rows=7 width=64)
                                               (actual time=0.041..0.089 rows=7 loops=1)
  Index Cond: (user_id = 42)
Planning Time: 0.4 ms
Execution Time: 0.1 ms
```

---

### Writing data (readwrite connection required)

```
Tool: execute
  connection: "local"
  sql:        "UPDATE products SET stock = stock - $1 WHERE id = $2"
  params:     [3, 99]
```
```
UPDATE 1
```

---

### Diagnosing slow queries

```
Tool: get_slow_queries
  connection: "prod"
  limit:      5
```
```json
[
  {
    "query":        "SELECT * FROM order_items WHERE product_id = $1",
    "calls":        18420,
    "mean_ms":      142.3,
    "total_ms":     2620716,
    "rows":         92100,
    "cache_hit_pct": 61.2
  },
  {
    "query":        "UPDATE orders SET status = $1 WHERE id = $2",
    "calls":        5301,
    "mean_ms":      38.7,
    "total_ms":     205248,
    "rows":         5301,
    "cache_hit_pct": 98.4
  }
]
```

---

### Checking active queries

```
Tool: get_active_queries
  connection: "prod"
```
```json
[
  {
    "pid":      28441,
    "state":    "active",
    "duration": "00:00:03.21",
    "query":    "SELECT COUNT(*) FROM order_items GROUP BY product_id",
    "wait_event": null
  },
  {
    "pid":      28502,
    "state":    "idle in transaction",
    "duration": "00:01:48.05",
    "query":    "BEGIN",
    "wait_event": "ClientRead"
  }
]
```

---

### Inspecting locks

```
Tool: get_locks
  connection: "prod"
```
```json
[
  {
    "pid":        28502,
    "relation":   "orders",
    "lock_type":  "RowExclusiveLock",
    "granted":    true,
    "mode":       "RowExclusiveLock",
    "blocked_by": null
  },
  {
    "pid":        28611,
    "relation":   "orders",
    "lock_type":  "AccessExclusiveLock",
    "granted":    false,
    "mode":       "AccessExclusiveLock",
    "blocked_by": 28502
  }
]
```

---

### Database info

```
Tool: get_database_info
  connection: "prod"
```
```json
{
  "version":      "PostgreSQL 16.2 on x86_64-pc-linux-gnu",
  "database":     "appdb",
  "size":         "2341 MB",
  "encoding":     "UTF8",
  "connections":  { "active": 12, "max": 100 },
  "extensions": [
    { "name": "pg_stat_statements", "version": "1.10" },
    { "name": "pgcrypto",           "version": "1.3"  },
    { "name": "uuid-ossp",          "version": "1.1"  }
  ]
}
```

---

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
