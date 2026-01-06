# src/database/__init__.py
"""Database layer for EPG service."""

from .connection import DatabaseConnection, close_db, get_db, initialize_db
from .models import Channel, ChannelAlias, ChannelMapping, ImportLog, Program, Provider
from .schema import SchemaManager

__all__ = [
    "SchemaManager",
    "Provider",
    "Channel",
    "ChannelMapping",
    "ChannelAlias",
    "Program",
    "ImportLog",
    "DatabaseConnection",
    "initialize_db",
    "get_db",
    "close_db",
]
