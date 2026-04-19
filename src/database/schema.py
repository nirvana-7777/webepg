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

    SCHEMA_VERSION = 6

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
        actors TEXT,  -- Will store JSON array
        directors TEXT,  -- Will store JSON array
        presenters TEXT,  -- NEW: JSON array of presenters
        writers TEXT,  -- NEW: JSON array of writers
        producers TEXT,  -- NEW: JSON array of producers
        icon_url TEXT,
        production_year TEXT,  -- NEW: Production year (date from XML)
        country TEXT,  -- NEW: Country of origin
        language TEXT, -- NEW: Language of the program
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

    -- Prevent duplicate programs
    CREATE UNIQUE INDEX IF NOT EXISTS idx_programs_unique
        ON programs(channel_id, start_time, end_time);

    CREATE INDEX IF NOT EXISTS idx_programs_fuzzy_match
    ON programs(channel_id, provider_id, title, start_time);

    CREATE INDEX IF NOT EXISTS idx_programs_time_range
    ON programs(channel_id, start_time);
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
                # No version table or no version recorded - insert current version
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (cls.SCHEMA_VERSION,),
                )
                logger.info(f"Initialized database with schema version {cls.SCHEMA_VERSION}")
            elif current_version < cls.SCHEMA_VERSION:
                # Run migrations sequentially
                logger.info(f"Database at version {current_version}, migrating to {cls.SCHEMA_VERSION}")
                cls._migrate_database(conn, current_version, cls.SCHEMA_VERSION)
            else:
                logger.info(f"Database already at version {current_version}")

            conn.commit()
        finally:
            conn.close()

    @classmethod
    def _migrate_database(
            cls, conn: sqlite3.Connection, from_version: int, to_version: int
    ):
        """
        Run database migrations sequentially from from_version to to_version.

        This method recursively calls itself to ensure all migrations run in order.
        """
        logger.info(f"Migrating database from version {from_version} to {to_version}")

        # Migration: version 1 -> 2
        if from_version == 1 and to_version >= 2:
            logger.info("Applying migration 1 -> 2")
            cls._migrate_v1_to_v2(conn)

            # Update version to 2
            cls._update_schema_version(conn, 2)

            # Continue to next migration if needed
            if to_version > 2:
                cls._migrate_database(conn, 2, to_version)
            return

        # Migration: version 2 -> 3
        if from_version == 2 and to_version >= 3:
            logger.info("Applying migration 2 -> 3")
            from ..database.migrations.v3_ultimate_backend import migrate_v2_to_v3
            migrate_v2_to_v3(conn)

            # Update version to 3
            cls._update_schema_version(conn, 3)

            # Continue to next migration if needed
            if to_version > 3:
                cls._migrate_database(conn, 3, to_version)
            return

        # Migration: version 3 -> 4
        if from_version == 3 and to_version >= 4:
            logger.info("Applying migration 3 -> 4")
            from ..database.migrations.v4_unified_providers import migrate_v3_to_v4
            migrate_v3_to_v4(conn)

            # Update version to 4
            cls._update_schema_version(conn, 4)

            # Continue to next migration if needed
            if to_version > 4:
                cls._migrate_database(conn, 4, to_version)
            return

        # Migration: version 4 -> 5
        if from_version == 4 and to_version >= 5:
            logger.info("Applying migration 4 -> 5")
            from ..database.migrations.v5_add_channel_updated_at import migrate_v4_to_v5
            migrate_v4_to_v5(conn)

            # Update version to 5
            cls._update_schema_version(conn, 5)

            # Continue to next migration if needed
            if to_version > 5:
                cls._migrate_database(conn, 5, to_version)
            return

        # Migration: version 5 -> 6
        if from_version == 5 and to_version >= 6:
            logger.info("Applying migration 5 -> 6")
            from ..database.migrations.v6_add_program_language import migrate_v5_to_v6
            migrate_v5_to_v6(conn)

            # Update version to 6
            cls._update_schema_version(conn, 6)

            # Continue to next migration if needed
            if to_version > 6:
                cls._migrate_database(conn, 6, to_version)
            return

        # If we get here, no migration path was found
        if from_version < to_version:
            logger.error(f"No migration path from version {from_version} to {to_version}")
            raise ValueError(f"Cannot migrate from version {from_version} to {to_version}")

    @classmethod
    def _migrate_v1_to_v2(cls, conn: sqlite3.Connection):
        """Migration from version 1 to 2."""
        cursor = conn.cursor()

        # Add new columns - check if they exist first to make migration idempotent
        new_columns = [
            ("presenters", "TEXT"),
            ("writers", "TEXT"),
            ("producers", "TEXT"),
            ("production_year", "TEXT"),
            ("country", "TEXT"),
        ]

        for column_name, column_type in new_columns:
            # Check if column already exists
            cursor.execute("PRAGMA table_info(programs)")
            existing_columns = [row[1] for row in cursor.fetchall()]

            if column_name not in existing_columns:
                cursor.execute(
                    f"ALTER TABLE programs ADD COLUMN {column_name} {column_type}"
                )
                logger.info(f"Added column {column_name} to programs table")
            else:
                logger.info(f"Column {column_name} already exists, skipping")

        conn.commit()
        logger.info("Migration to version 2 completed")

    @classmethod
    def _update_schema_version(cls, conn: sqlite3.Connection, version: int):
        """Update or insert the schema version."""
        cursor = conn.cursor()

        # Check if version already exists
        cursor.execute("SELECT 1 FROM schema_version WHERE version = ?", (version,))
        if cursor.fetchone():
            # Version already recorded, update timestamp
            cursor.execute(
                "UPDATE schema_version SET applied_at = CURRENT_TIMESTAMP WHERE version = ?",
                (version,)
            )
        else:
            # Insert new version
            cursor.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (version,)
            )

        conn.commit()
        logger.info(f"Schema version updated to {version}")

    @classmethod
    def _get_schema_version(cls, conn: sqlite3.Connection) -> Optional[int]:
        """Get current schema version from database."""
        try:
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            result = cursor.fetchone()
            return result[0] if result and result[0] is not None else None
        except sqlite3.OperationalError:
            # Table doesn't exist yet
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
            is_current = current_version == cls.SCHEMA_VERSION

            if not is_current:
                logger.warning(
                    f"Schema verification failed: current version={current_version}, "
                    f"expected={cls.SCHEMA_VERSION}"
                )

            return is_current
        finally:
            conn.close()