"""
Database schema definition and migration management for EPG service.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SchemaManager:
    """Manages database schema creation and migrations."""

    SCHEMA_VERSION = 1

    SCHEMA_SQL = """
    -- Schema version tracking
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    
    -- Providers table
    CREATE TABLE IF NOT EXISTS providers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        xmltv_url TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    
    -- Logical channels (user-facing)
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        display_name TEXT NOT NULL,
        icon_url TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(name)
    );
    
    -- Channel aliases for flexible API access
    CREATE TABLE IF NOT EXISTS channel_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id INTEGER NOT NULL,
        alias TEXT NOT NULL UNIQUE,
        alias_type TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
    );
    
    -- Map provider channel IDs to logical channels
    CREATE TABLE IF NOT EXISTS channel_mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider_id INTEGER NOT NULL,
        provider_channel_id TEXT NOT NULL,
        channel_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE,
        FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE,
        UNIQUE(provider_id, provider_channel_id)
    );
    
    -- EPG program data
    CREATE TABLE IF NOT EXISTS programs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id INTEGER NOT NULL,
        provider_id INTEGER NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        title TEXT NOT NULL,
        subtitle TEXT,
        description TEXT,
        category TEXT,
        episode_num TEXT,
        rating TEXT,
        actors TEXT,
        directors TEXT,
        icon_url TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE,
        FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE
    );
    
    -- Import tracking
    CREATE TABLE IF NOT EXISTS import_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider_id INTEGER NOT NULL,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT,
        status TEXT NOT NULL,
        programs_imported INTEGER DEFAULT 0,
        programs_skipped INTEGER DEFAULT 0,
        error_message TEXT,
        FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE
    );
    """

    INDEXES_SQL = """
    -- Composite index for efficient time-range queries
    CREATE INDEX IF NOT EXISTS idx_programs_channel_time 
        ON programs(channel_id, start_time, end_time);
    
    CREATE INDEX IF NOT EXISTS idx_programs_provider_time 
        ON programs(provider_id, start_time);
    
    CREATE INDEX IF NOT EXISTS idx_import_log_provider 
        ON import_log(provider_id, completed_at);
    
    CREATE INDEX IF NOT EXISTS idx_channel_mappings_lookup
        ON channel_mappings(provider_id, provider_channel_id);
    
    CREATE INDEX IF NOT EXISTS idx_channel_aliases_lookup
        ON channel_aliases(alias);
    """

    @classmethod
    def initialize_database(cls, db_path: str) -> None:
        """
        Initialize database with schema and indexes.

        Args:
            db_path: Path to SQLite database file
        """
        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Set timeout to avoid lock issues
        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            # Enable foreign keys
            conn.execute("PRAGMA foreign_keys = ON")

            # Set busy timeout
            conn.execute("PRAGMA busy_timeout = 30000")

            # Enable WAL mode for better concurrency
            # WAL mode may fail if database is locked, so wrap in try-except
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError as e:
                logger.warning(
                    f"Could not set WAL mode: {e}. Continuing with default journal mode."
                )

            # Create schema
            conn.executescript(cls.SCHEMA_SQL)

            # Create indexes
            conn.executescript(cls.INDEXES_SQL)

            # Record schema version
            current_version = cls._get_schema_version(conn)
            if current_version is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (cls.SCHEMA_VERSION,),
                )

            conn.commit()
        finally:
            conn.close()

    @classmethod
    def _get_schema_version(cls, conn: sqlite3.Connection) -> Optional[int]:
        """Get current schema version from database."""
        try:
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            result = cursor.fetchone()
            return result[0] if result else None
        except sqlite3.OperationalError:
            return None

    @classmethod
    def verify_schema(cls, db_path: str) -> bool:
        """
        Verify that database schema is up to date.

        Args:
            db_path: Path to SQLite database file

        Returns:
            True if schema is current, False otherwise
        """
        conn = sqlite3.connect(db_path)
        try:
            current_version = cls._get_schema_version(conn)
            return current_version == cls.SCHEMA_VERSION
        finally:
            conn.close()
