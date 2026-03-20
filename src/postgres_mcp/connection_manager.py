"""Manages multiple PostgreSQL connection pools via asyncpg."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
from dotenv import dotenv_values

from postgres_mcp.models import AccessMode, ConnectionInfo, ConnectionMetadata

logger = logging.getLogger("postgres_mcp")


def _normalize_dsn(dsn: str) -> str:
    """Convert a SQLAlchemy-style DSN to a plain postgresql:// DSN for asyncpg.

    Strips driver suffixes like ``+psycopg2``, ``+asyncpg``, ``+pg8000``, etc.
    """
    return re.sub(r"^postgresql\+\w+://", "postgresql://", dsn)


class ConnectionManager:
    """Holds named asyncpg connection pools with access-mode enforcement."""

    def __init__(self) -> None:
        self._pools: dict[str, asyncpg.Pool] = {}
        self._meta: dict[str, ConnectionMetadata] = {}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def connect(
        self,
        alias: str,
        dsn: str,
        mode: str = "readonly",
    ) -> str:
        """Create a connection pool and register it under *alias*."""
        if alias in self._pools:
            raise ValueError(f"Connection '{alias}' already exists. Disconnect first.")

        access = AccessMode(mode)
        dsn = _normalize_dsn(dsn)
        parsed = urlparse(dsn)

        pool_min = int(os.getenv("PG_MCP_POOL_MIN", "1"))
        pool_max = int(os.getenv("PG_MCP_POOL_MAX", "5"))

        async def _setup(conn: asyncpg.Connection) -> None:
            if access == AccessMode.READONLY:
                await conn.execute("SET default_transaction_read_only = ON")
            timeout = int(os.getenv("PG_MCP_QUERY_TIMEOUT", "30"))
            await conn.execute(f"SET statement_timeout = '{timeout}s'")

        pool = await asyncpg.create_pool(
            dsn,
            min_size=pool_min,
            max_size=pool_max,
            setup=_setup,
            max_inactive_connection_lifetime=300,
        )

        self._pools[alias] = pool
        self._meta[alias] = ConnectionMetadata(
            alias=alias,
            host=parsed.hostname or "localhost",
            port=parsed.port or 5432,
            database=(parsed.path or "/").lstrip("/") or "postgres",
            user=parsed.username or "unknown",
            mode=access,
            created_at=datetime.now(timezone.utc),
        )
        logger.info("Connected '%s' → %s@%s/%s (%s)",
                     alias, parsed.username, parsed.hostname, parsed.path, mode)
        return f"Connected as '{alias}' ({mode})"

    async def disconnect(self, alias: str) -> str:
        pool = self._pools.pop(alias, None)
        self._meta.pop(alias, None)
        if pool is None:
            raise ValueError(f"No connection named '{alias}'")
        await pool.close()
        logger.info("Disconnected '%s'", alias)
        return f"Disconnected '{alias}'"

    async def list_connections(self) -> list[ConnectionInfo]:
        results: list[ConnectionInfo] = []
        for alias, meta in self._meta.items():
            pool = self._pools[alias]
            status = "active" if pool.get_size() > 0 else "idle"
            results.append(ConnectionInfo(
                alias=meta.alias,
                host=meta.host,
                port=meta.port,
                database=meta.database,
                user=meta.user,
                mode=meta.mode.value,
                status=status,
            ))
        return results

    async def get_pool(self, alias: str) -> asyncpg.Pool:
        pool = self._pools.get(alias)
        if pool is None:
            raise ValueError(
                f"No connection named '{alias}'. "
                f"Available: {', '.join(self._pools) or '(none)'}"
            )
        meta = self._meta[alias]
        meta.last_used = datetime.now(timezone.utc)
        return pool

    def get_mode(self, alias: str) -> AccessMode:
        meta = self._meta.get(alias)
        if meta is None:
            raise ValueError(f"No connection named '{alias}'")
        return meta.mode

    async def close_all(self) -> None:
        for alias in list(self._pools):
            pool = self._pools.pop(alias)
            self._meta.pop(alias, None)
            await pool.close()
        logger.info("All connections closed")

    # ------------------------------------------------------------------ #
    # Startup helpers
    # ------------------------------------------------------------------ #

    async def load_from_env(self) -> None:
        """Auto-register connections from environment and .env files.

        Sources (checked in order):
        1. A ``.env`` file in the current working directory — looks for
           ``SQLALCHEMY_DATABASE_URI`` or ``DATABASE_URL`` and registers it
           as the ``default`` connection.
        2. ``PG_MCP_CONN_*`` environment variables for explicit named connections.

        SQLAlchemy-style DSNs (``postgresql+psycopg2://…``) are automatically
        converted to plain ``postgresql://`` for asyncpg.
        """
        default_mode = os.getenv("PG_MCP_DEFAULT_MODE", "readonly")

        # --- .env auto-discovery -------------------------------------------
        env_file = Path.cwd() / ".env"
        if env_file.is_file():
            dotenv_vars = dotenv_values(env_file)
            for var_name in ("SQLALCHEMY_DATABASE_URI", "DATABASE_URL"):
                dsn = dotenv_vars.get(var_name)
                if dsn and "default" not in self._pools:
                    # Derive a friendly alias from the database name
                    parsed = urlparse(_normalize_dsn(dsn))
                    db_name = (parsed.path or "/").lstrip("/") or "default"
                    alias = db_name
                    try:
                        await self.connect(alias, dsn, default_mode)
                        logger.info(
                            "Auto-connected '%s' from .env %s", alias, var_name
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to auto-connect from .env %s: %s",
                            var_name, exc,
                        )
                    break  # Only use the first one found

        # --- PG_MCP_CONN_* explicit connections ----------------------------
        for key, dsn in os.environ.items():
            if not key.startswith("PG_MCP_CONN_"):
                continue
            if key.endswith("__MODE"):
                continue
            alias = key[len("PG_MCP_CONN_"):].lower()
            mode = os.getenv(f"{key}__MODE", default_mode)
            try:
                await self.connect(alias, dsn, mode)
            except Exception as exc:
                logger.error("Failed to auto-connect '%s': %s", alias, exc)
