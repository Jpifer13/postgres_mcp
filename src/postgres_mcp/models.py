"""Pydantic models for connection metadata and tool outputs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class AccessMode(str, Enum):
    READONLY = "readonly"
    READWRITE = "readwrite"


class ConnectionMetadata(BaseModel):
    alias: str
    host: str
    port: int
    database: str
    user: str
    mode: AccessMode
    created_at: datetime
    last_used: datetime | None = None


class ConnectionInfo(BaseModel):
    alias: str
    host: str
    port: int
    database: str
    user: str
    mode: str
    status: str
