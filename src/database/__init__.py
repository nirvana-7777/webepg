# src/database/__init__.py
"""Database layer for EPG service."""

from .schema import SchemaManager
from .models import Provider, Channel, ChannelMapping, ChannelAlias, Program, ImportLog
from .connection import DatabaseConnection, initialize_db, get_db, close_db

__all__ = [
    'SchemaManager',
    'Provider',
    'Channel',
    'ChannelMapping',
    'ChannelAlias',
    'Program',
    'ImportLog',
    'DatabaseConnection',
    'initialize_db',
    'get_db',
    'close_db'
]