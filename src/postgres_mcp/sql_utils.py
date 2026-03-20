"""Lightweight SQL validation helpers (defense-in-depth, not a full parser)."""

from __future__ import annotations

import re

# Statements that are safe for the `query` tool
_SELECT_PREFIXES = re.compile(
    r"^\s*(SELECT|WITH|EXPLAIN|SHOW|VALUES)\b", re.IGNORECASE
)

# DDL keywords blocked in `execute` unless explicitly allowed
_DDL_KEYWORDS = re.compile(
    r"^\s*(CREATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|COMMENT|REASSIGN|SECURITY)\b",
    re.IGNORECASE,
)


def classify_sql(sql: str) -> str:
    """Return 'select', 'dml', 'ddl', or 'other'."""
    stripped = sql.strip()
    if _SELECT_PREFIXES.match(stripped):
        return "select"
    if _DDL_KEYWORDS.match(stripped):
        return "ddl"
    upper = stripped.split()[0].upper() if stripped else ""
    if upper in ("INSERT", "UPDATE", "DELETE"):
        return "dml"
    return "other"


def is_single_statement(sql: str) -> bool:
    """Reject multi-statement strings.

    Simple heuristic: strip string literals and comments, then check for
    a semicolon that isn't at the very end.
    """
    # Remove single-quoted strings (including escaped quotes)
    cleaned = re.sub(r"'(?:[^'\\]|\\.)*'", "''", sql)
    # Remove double-quoted identifiers
    cleaned = re.sub(r'"(?:[^"\\]|\\.)*"', '""', cleaned)
    # Remove -- line comments
    cleaned = re.sub(r"--[^\n]*", "", cleaned)
    # Remove /* */ block comments
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)

    # Strip trailing whitespace and optional trailing semicolon
    cleaned = cleaned.rstrip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1]

    return ";" not in cleaned


def validate_read_query(sql: str) -> None:
    """Raise ValueError if *sql* is not a read-only statement."""
    kind = classify_sql(sql)
    if kind != "select":
        raise ValueError(
            f"Only SELECT / WITH / EXPLAIN / SHOW queries are allowed. "
            f"Got: {sql[:80]!r}"
        )
    if not is_single_statement(sql):
        raise ValueError("Multi-statement queries are not allowed.")


def validate_write_query(sql: str, allow_ddl: bool = False) -> None:
    """Raise ValueError if *sql* is not a safe write statement."""
    kind = classify_sql(sql)
    if kind == "select":
        raise ValueError("Use the 'query' tool for SELECT statements.")
    if kind == "ddl" and not allow_ddl:
        raise ValueError(
            "DDL statements (CREATE, DROP, ALTER, etc.) are not allowed. "
            "Set allow_ddl=True to override."
        )
    if kind == "other":
        raise ValueError(f"Unrecognized statement type: {sql[:80]!r}")
    if not is_single_statement(sql):
        raise ValueError("Multi-statement queries are not allowed.")
