"""SQL constants for PostgreSQL catalog introspection."""

LIST_SCHEMAS = """
SELECT
    n.nspname AS schema_name,
    pg_catalog.obj_description(n.oid) AS description,
    (SELECT count(*) FROM pg_catalog.pg_class c
     WHERE c.relnamespace = n.oid AND c.relkind IN ('r','p')) AS table_count,
    (SELECT count(*) FROM pg_catalog.pg_class c
     WHERE c.relnamespace = n.oid AND c.relkind = 'v') AS view_count
FROM pg_catalog.pg_namespace n
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND n.nspname NOT LIKE 'pg_temp_%'
  AND n.nspname NOT LIKE 'pg_toast_temp_%'
ORDER BY n.nspname;
"""

LIST_TABLES = """
SELECT
    c.relname AS table_name,
    CASE c.relkind
        WHEN 'r' THEN 'table'
        WHEN 'v' THEN 'view'
        WHEN 'm' THEN 'materialized_view'
        WHEN 'p' THEN 'partitioned_table'
        WHEN 'f' THEN 'foreign_table'
    END AS type,
    pg_catalog.obj_description(c.oid) AS description,
    c.reltuples::bigint AS estimated_rows,
    pg_catalog.pg_size_pretty(pg_catalog.pg_total_relation_size(c.oid)) AS total_size
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = $1
  AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
ORDER BY c.relname;
"""

DESCRIBE_COLUMNS = """
SELECT
    a.attname AS column_name,
    pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
    NOT a.attnotnull AS is_nullable,
    pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS column_default,
    pg_catalog.col_description(a.attrelid, a.attnum) AS description,
    a.attnum AS ordinal_position
FROM pg_catalog.pg_attribute a
LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attrelid = $1::regclass
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY a.attnum;
"""

DESCRIBE_PRIMARY_KEY = """
SELECT
    conname AS constraint_name,
    array_agg(a.attname ORDER BY x.ord) AS columns
FROM pg_catalog.pg_constraint con
JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS x(attnum, ord) ON true
JOIN pg_catalog.pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = x.attnum
WHERE con.conrelid = $1::regclass
  AND con.contype = 'p'
GROUP BY con.conname;
"""

DESCRIBE_FOREIGN_KEYS = """
SELECT
    con.conname AS constraint_name,
    array_agg(a.attname ORDER BY x.ord) AS columns,
    confrel.relname AS referenced_table,
    confns.nspname AS referenced_schema,
    array_agg(af.attname ORDER BY x.ord) AS referenced_columns
FROM pg_catalog.pg_constraint con
JOIN LATERAL unnest(con.conkey, con.confkey) WITH ORDINALITY AS x(attnum, fattnum, ord) ON true
JOIN pg_catalog.pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = x.attnum
JOIN pg_catalog.pg_class confrel ON confrel.oid = con.confrelid
JOIN pg_catalog.pg_namespace confns ON confns.oid = confrel.relnamespace
JOIN pg_catalog.pg_attribute af ON af.attrelid = con.confrelid AND af.attnum = x.fattnum
WHERE con.conrelid = $1::regclass
  AND con.contype = 'f'
GROUP BY con.conname, confrel.relname, confns.nspname;
"""

DESCRIBE_INDEXES = """
SELECT
    i.relname AS index_name,
    ix.indisunique AS is_unique,
    ix.indisprimary AS is_primary,
    am.amname AS index_type,
    array_agg(a.attname ORDER BY x.ord) AS columns,
    pg_catalog.pg_size_pretty(pg_catalog.pg_relation_size(i.oid)) AS index_size,
    pg_catalog.pg_get_indexdef(ix.indexrelid) AS definition
FROM pg_catalog.pg_index ix
JOIN pg_catalog.pg_class i ON i.oid = ix.indexrelid
JOIN pg_catalog.pg_am am ON am.oid = i.relam
JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS x(attnum, ord) ON true
JOIN pg_catalog.pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = x.attnum
WHERE ix.indrelid = $1::regclass
GROUP BY i.relname, ix.indisunique, ix.indisprimary, am.amname, i.oid, ix.indexrelid
ORDER BY i.relname;
"""

DESCRIBE_CONSTRAINTS = """
SELECT
    con.conname AS constraint_name,
    CASE con.contype
        WHEN 'c' THEN 'check'
        WHEN 'f' THEN 'foreign_key'
        WHEN 'p' THEN 'primary_key'
        WHEN 'u' THEN 'unique'
        WHEN 'x' THEN 'exclusion'
    END AS constraint_type,
    pg_catalog.pg_get_constraintdef(con.oid) AS definition
FROM pg_catalog.pg_constraint con
WHERE con.conrelid = $1::regclass
ORDER BY con.contype, con.conname;
"""

DESCRIBE_TRIGGERS = """
SELECT
    t.tgname AS trigger_name,
    pg_catalog.pg_get_triggerdef(t.oid) AS definition
FROM pg_catalog.pg_trigger t
WHERE t.tgrelid = $1::regclass
  AND NOT t.tgisinternal
ORDER BY t.tgname;
"""

LIST_INDEXES_SCHEMA = """
SELECT
    c.relname AS table_name,
    i.relname AS index_name,
    ix.indisunique AS is_unique,
    am.amname AS index_type,
    array_agg(a.attname ORDER BY x.ord) AS columns,
    pg_catalog.pg_size_pretty(pg_catalog.pg_relation_size(i.oid)) AS index_size,
    COALESCE(s.idx_scan, 0) AS index_scans
FROM pg_catalog.pg_index ix
JOIN pg_catalog.pg_class i ON i.oid = ix.indexrelid
JOIN pg_catalog.pg_class c ON c.oid = ix.indrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_am am ON am.oid = i.relam
JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS x(attnum, ord) ON true
JOIN pg_catalog.pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = x.attnum
LEFT JOIN pg_catalog.pg_stat_user_indexes s ON s.indexrelid = ix.indexrelid
WHERE n.nspname = $1
GROUP BY c.relname, i.relname, ix.indisunique, am.amname, i.oid, s.idx_scan
ORDER BY c.relname, i.relname;
"""

LIST_INDEXES_TABLE = """
SELECT
    c.relname AS table_name,
    i.relname AS index_name,
    ix.indisunique AS is_unique,
    am.amname AS index_type,
    array_agg(a.attname ORDER BY x.ord) AS columns,
    pg_catalog.pg_size_pretty(pg_catalog.pg_relation_size(i.oid)) AS index_size,
    COALESCE(s.idx_scan, 0) AS index_scans
FROM pg_catalog.pg_index ix
JOIN pg_catalog.pg_class i ON i.oid = ix.indexrelid
JOIN pg_catalog.pg_class c ON c.oid = ix.indrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_am am ON am.oid = i.relam
JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS x(attnum, ord) ON true
JOIN pg_catalog.pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = x.attnum
LEFT JOIN pg_catalog.pg_stat_user_indexes s ON s.indexrelid = ix.indexrelid
WHERE n.nspname = $1 AND c.relname = $2
GROUP BY c.relname, i.relname, ix.indisunique, am.amname, i.oid, s.idx_scan
ORDER BY i.relname;
"""

LIST_CONSTRAINTS_SCHEMA = """
SELECT
    c.relname AS table_name,
    con.conname AS constraint_name,
    CASE con.contype
        WHEN 'c' THEN 'check'
        WHEN 'f' THEN 'foreign_key'
        WHEN 'p' THEN 'primary_key'
        WHEN 'u' THEN 'unique'
        WHEN 'x' THEN 'exclusion'
    END AS constraint_type,
    pg_catalog.pg_get_constraintdef(con.oid) AS definition
FROM pg_catalog.pg_constraint con
JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = $1
ORDER BY c.relname, con.contype, con.conname;
"""

LIST_CONSTRAINTS_TABLE = """
SELECT
    c.relname AS table_name,
    con.conname AS constraint_name,
    CASE con.contype
        WHEN 'c' THEN 'check'
        WHEN 'f' THEN 'foreign_key'
        WHEN 'p' THEN 'primary_key'
        WHEN 'u' THEN 'unique'
        WHEN 'x' THEN 'exclusion'
    END AS constraint_type,
    pg_catalog.pg_get_constraintdef(con.oid) AS definition
FROM pg_catalog.pg_constraint con
JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = $1 AND c.relname = $2
ORDER BY con.contype, con.conname;
"""

LIST_FUNCTIONS = """
SELECT
    p.proname AS function_name,
    pg_catalog.pg_get_function_arguments(p.oid) AS arguments,
    pg_catalog.pg_get_function_result(p.oid) AS return_type,
    l.lanname AS language,
    CASE p.prokind
        WHEN 'f' THEN 'function'
        WHEN 'p' THEN 'procedure'
        WHEN 'a' THEN 'aggregate'
        WHEN 'w' THEN 'window'
    END AS kind,
    pg_catalog.obj_description(p.oid) AS description
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
JOIN pg_catalog.pg_language l ON l.oid = p.prolang
WHERE n.nspname = $1
ORDER BY p.proname;
"""

LIST_ENUMS = """
SELECT
    t.typname AS enum_name,
    array_agg(e.enumlabel ORDER BY e.enumsortorder) AS values
FROM pg_catalog.pg_type t
JOIN pg_catalog.pg_enum e ON e.enumtypid = t.oid
JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
WHERE n.nspname = $1
GROUP BY t.typname
ORDER BY t.typname;
"""

TABLE_STATS = """
SELECT
    s.n_live_tup AS live_rows,
    s.n_dead_tup AS dead_rows,
    s.last_vacuum,
    s.last_autovacuum,
    s.last_analyze,
    s.last_autoanalyze,
    s.seq_scan,
    s.idx_scan,
    pg_catalog.pg_size_pretty(pg_catalog.pg_total_relation_size(c.oid)) AS total_size,
    pg_catalog.pg_size_pretty(pg_catalog.pg_table_size(c.oid)) AS table_size,
    pg_catalog.pg_size_pretty(pg_catalog.pg_indexes_size(c.oid)) AS indexes_size
FROM pg_catalog.pg_stat_user_tables s
JOIN pg_catalog.pg_class c ON c.relname = s.relname AND c.relnamespace = s.relid::regclass::oid
WHERE s.schemaname = $1 AND s.relname = $2;
"""

# Simpler stats query that's more reliable
TABLE_STATS_SIMPLE = """
SELECT
    s.n_live_tup AS live_rows,
    s.n_dead_tup AS dead_rows,
    s.last_vacuum,
    s.last_autovacuum,
    s.last_analyze,
    s.last_autoanalyze,
    s.seq_scan,
    s.idx_scan
FROM pg_catalog.pg_stat_user_tables s
WHERE s.schemaname = $1 AND s.relname = $2;
"""

DATABASE_INFO = """
SELECT
    current_database() AS database_name,
    version() AS version,
    pg_catalog.pg_encoding_to_char(d.encoding) AS encoding,
    d.datcollate AS collation,
    pg_catalog.pg_size_pretty(pg_catalog.pg_database_size(current_database())) AS database_size,
    (SELECT count(*) FROM pg_catalog.pg_stat_activity
     WHERE datname = current_database()) AS active_connections
FROM pg_catalog.pg_database d
WHERE d.datname = current_database();
"""

INSTALLED_EXTENSIONS = """
SELECT extname AS name, extversion AS version
FROM pg_catalog.pg_extension
ORDER BY extname;
"""

SLOW_QUERIES = """
SELECT
    query,
    calls,
    round(total_exec_time::numeric, 2) AS total_time_ms,
    round(mean_exec_time::numeric, 2) AS mean_time_ms,
    rows
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT $1;
"""

ACTIVE_QUERIES = """
SELECT
    pid,
    usename AS user,
    datname AS database,
    state,
    query,
    now() - query_start AS duration,
    wait_event_type,
    wait_event
FROM pg_catalog.pg_stat_activity
WHERE datname = current_database()
  AND pid != pg_backend_pid()
  AND state IS NOT NULL
ORDER BY query_start;
"""

LOCKS = """
SELECT
    l.pid,
    a.usename AS user,
    l.locktype,
    l.mode,
    l.granted,
    l.relation::regclass AS relation,
    a.query,
    now() - a.query_start AS duration
FROM pg_catalog.pg_locks l
JOIN pg_catalog.pg_stat_activity a ON a.pid = l.pid
WHERE a.datname = current_database()
  AND l.pid != pg_backend_pid()
ORDER BY a.query_start;
"""
