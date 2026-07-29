"""
Database schema definition and migration management for EPG service.
"""

import contextlib
import logging
import sqlite3
from pathlib import Path
from typing import Optional

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover - non-POSIX platforms
    _HAS_FCNTL = False

logger = logging.getLogger(__name__)


class SchemaManager:
    """Manages database schema creation and migrations."""

    SCHEMA_VERSION = 8
    # The version that SCHEMA_SQL represents (the initial/baseline schema).
    # Migrations run from this version up to SCHEMA_VERSION on fresh installs,
    # so SCHEMA_SQL should stay a faithful "version 1" snapshot -- do not fold
    # later-migration columns back into it without also bumping this constant
    # and re-verifying the full migration chain end to end.
    INITIAL_SCHEMA_VERSION = 1

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
        actors TEXT,  -- JSON array
        directors TEXT,  -- JSON array
        presenters TEXT,  -- JSON array of presenters
        writers TEXT,  -- JSON array of writers
        producers TEXT,  -- JSON array of producers
        icon_url TEXT,
        production_year TEXT,
        country TEXT,
        language TEXT,
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

    # ------------------------------------------------------------------
    # Cross-process locking
    # ------------------------------------------------------------------
    #
    # SQLite's own locking protects individual statements/transactions, but
    # it does not serialize the higher-level "check version, then maybe run
    # a multi-statement migration" sequence used below. Two processes
    # starting up against the same fresh database (e.g. two containers
    # sharing a volume) could both observe `current_version is None` and
    # both attempt to create the baseline schema / run migrations
    # concurrently. An flock-based sidecar lock file serializes
    # `initialize_database` across processes on the same host without
    # interfering with the migrations' own internal commits (which a
    # nested `BEGIN IMMEDIATE` transaction would break).
    @staticmethod
    @contextlib.contextmanager
    def _init_lock(db_path: str):
        if not _HAS_FCNTL:
            logger.warning(
                "fcntl unavailable on this platform; skipping cross-process "
                "init lock. Ensure only one process initializes this "
                "database at a time."
            )
            yield
            return

        lock_path = f"{db_path}.initlock"
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        fd = open(lock_path, "w")
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()

    @classmethod
    def initialize_database(cls, db_path: str) -> None:
        """
        Initialize database with schema and indexes.

        On a fresh database (no schema_version table), this creates the
        initial baseline schema (version INITIAL_SCHEMA_VERSION) and then
        runs all migrations up to SCHEMA_VERSION. This ensures fresh
        installs get the complete, current schema rather than a partial
        one mismatched with the recorded version.

        Safe to call concurrently from multiple processes on the same
        host; a sidecar lock file serializes initialization.

        Args:
            db_path: Path to SQLite database file
        """
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        with cls._init_lock(db_path):
            conn = sqlite3.connect(db_path, timeout=30.0)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA busy_timeout = 30000")

                try:
                    conn.execute("PRAGMA journal_mode = WAL")
                except sqlite3.OperationalError as e:
                    logger.warning(
                        f"Could not set WAL mode: {e}. Continuing with "
                        "default journal mode."
                    )

                # Check current schema version BEFORE creating any schema
                # objects. This tells us whether the DB is truly fresh or
                # pre-existing.
                current_version = cls._get_schema_version(conn)

                if current_version is None:
                    logger.info(
                        "No schema_version found - creating baseline schema "
                        f"(version {cls.INITIAL_SCHEMA_VERSION})"
                    )
                    conn.executescript(cls.SCHEMA_SQL)
                    conn.executescript(cls.INDEXES_SQL)

                    # Record the INITIAL schema version (NOT the target
                    # SCHEMA_VERSION). Migrations below advance it
                    # step-by-step to SCHEMA_VERSION.
                    cls._update_schema_version(conn, cls.INITIAL_SCHEMA_VERSION)
                    current_version = cls.INITIAL_SCHEMA_VERSION
                    logger.info(
                        "Initialized fresh database at baseline version "
                        f"{cls.INITIAL_SCHEMA_VERSION}"
                    )
                else:
                    # Pre-existing database with a recorded version. Ensure
                    # base tables/indexes exist (idempotent via IF NOT
                    # EXISTS) for safety.
                    conn.executescript(cls.SCHEMA_SQL)
                    conn.executescript(cls.INDEXES_SQL)

                if current_version < cls.SCHEMA_VERSION:
                    logger.info(
                        f"Database at version {current_version}, "
                        f"migrating to {cls.SCHEMA_VERSION}"
                    )
                    cls._migrate_database(conn, current_version, cls.SCHEMA_VERSION)
                elif current_version > cls.SCHEMA_VERSION:
                    logger.warning(
                        f"Database version {current_version} is NEWER than "
                        f"code version {cls.SCHEMA_VERSION}. Running in "
                        "compatibility mode."
                    )
                else:
                    logger.info(f"Database already at version {current_version}")

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @classmethod
    def _migrate_database(
        cls, conn: sqlite3.Connection, from_version: int, to_version: int
    ):
        """
        Run database migrations sequentially from from_version to to_version.

        Recurses to ensure all migrations run in order. Each individual
        migration function commits its own work, so a failure partway
        through this chain leaves the database at the last successfully
        completed version rather than rolling everything back -- retrying
        `initialize_database` will resume from there.
        """
        logger.info(f"Migrating database from version {from_version} to {to_version}")

        # Migration: version 1 -> 2
        if from_version == 1 and to_version >= 2:
            logger.info("Applying migration 1 -> 2")
            cls._migrate_v1_to_v2(conn)
            cls._update_schema_version(conn, 2)
            if to_version > 2:
                cls._migrate_database(conn, 2, to_version)
            return

        # Migration: version 2 -> 3
        if from_version == 2 and to_version >= 3:
            logger.info("Applying migration 2 -> 3")
            from ..database.migrations.v3_ultimate_backend import migrate_v2_to_v3

            migrate_v2_to_v3(conn)
            cls._update_schema_version(conn, 3)
            if to_version > 3:
                cls._migrate_database(conn, 3, to_version)
            return

        # Migration: version 3 -> 4
        if from_version == 3 and to_version >= 4:
            logger.info("Applying migration 3 -> 4")
            from ..database.migrations.v4_unified_providers import migrate_v3_to_v4

            migrate_v3_to_v4(conn)
            cls._update_schema_version(conn, 4)
            if to_version > 4:
                cls._migrate_database(conn, 4, to_version)
            return

        # Migration: version 4 -> 5
        if from_version == 4 and to_version >= 5:
            logger.info("Applying migration 4 -> 5")
            from ..database.migrations.v5_add_channel_updated_at import migrate_v4_to_v5

            migrate_v4_to_v5(conn)
            cls._update_schema_version(conn, 5)
            if to_version > 5:
                cls._migrate_database(conn, 5, to_version)
            return

        # Migration: version 5 -> 6
        if from_version == 5 and to_version >= 6:
            logger.info("Applying migration 5 -> 6")
            from ..database.migrations.v6_add_program_language import migrate_v5_to_v6

            migrate_v5_to_v6(conn)
            cls._update_schema_version(conn, 6)
            if to_version > 6:
                cls._migrate_database(conn, 6, to_version)
            return

        # Migration: version 6 -> 7
        if from_version == 6 and to_version >= 7:
            logger.info("Applying migration 6 -> 7")
            from ..database.migrations.v7_epg_details import migrate_v6_to_v7

            migrate_v6_to_v7(conn)
            cls._update_schema_version(conn, 7)
            if to_version > 7:
                cls._migrate_database(conn, 7, to_version)
            return

        # Migration: version 7 -> 8
        if from_version == 7 and to_version >= 8:
            logger.info("Applying migration 7 -> 8")
            from ..database.migrations.v8_channel_import_errors import migrate_v7_to_v8

            migrate_v7_to_v8(conn)
            cls._update_schema_version(conn, 8)
            if to_version > 8:
                cls._migrate_database(conn, 8, to_version)
            return

        # If we get here, no migration path was found
        if from_version < to_version:
            logger.error(
                f"No migration path from version {from_version} to {to_version}"
            )
            raise ValueError(
                f"Cannot migrate from version {from_version} to {to_version}"
            )

    @classmethod
    def _migrate_v1_to_v2(cls, conn: sqlite3.Connection):
        """Migration from version 1 to 2."""
        cursor = conn.cursor()

        new_columns = [
            ("presenters", "TEXT"),
            ("writers", "TEXT"),
            ("producers", "TEXT"),
            ("production_year", "TEXT"),
            ("country", "TEXT"),
        ]

        cursor.execute("PRAGMA table_info(programs)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        for column_name, column_type in new_columns:
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

        cursor.execute("SELECT 1 FROM schema_version WHERE version = ?", (version,))
        if cursor.fetchone():
            cursor.execute(
                "UPDATE schema_version SET applied_at = CURRENT_TIMESTAMP "
                "WHERE version = ?",
                (version,),
            )
        else:
            cursor.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (version,)
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
        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
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