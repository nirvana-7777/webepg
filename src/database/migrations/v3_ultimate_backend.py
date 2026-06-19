"""
Database migration for Ultimate Backend integration.
Version 2 → 3
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


def migrate_v2_to_v3(db_or_path):
    """Accept either connection object or path string."""
    if isinstance(db_or_path, str):
        conn = sqlite3.connect(db_or_path)
        _close_when_done = True
    else:
        conn = db_or_path
        _close_when_done = False

    cursor = conn.cursor()

    try:
        cursor.execute("PRAGMA foreign_keys = OFF")

        # ======================================================================
        # Create new tables
        # ======================================================================

        # 1. Ultimate Backend instances
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ultimate_backend_instances (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL UNIQUE,
                base_url    TEXT    NOT NULL,
                api_key     TEXT,
                enabled     INTEGER NOT NULL DEFAULT 1,
                priority    INTEGER NOT NULL DEFAULT 100,
                created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("Created table: ultimate_backend_instances")

        cursor.execute("""
            INSERT OR IGNORE INTO ultimate_backend_instances (name, base_url, enabled)
            VALUES ('main', 'http://ultimate:7777', 1)
        """)

        # 2. Ultimate providers
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ultimate_providers (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id             INTEGER NOT NULL,
                provider_name           TEXT    NOT NULL,
                provider_label          TEXT    NOT NULL,
                has_epg                 INTEGER NOT NULL DEFAULT 0,
                enabled                 INTEGER NOT NULL DEFAULT 1,
                last_discovered_at      TEXT,
                last_import_start       TEXT,
                last_import_end         TEXT,
                last_successful_import  TEXT,
                error_count             INTEGER NOT NULL DEFAULT 0,
                last_error              TEXT,
                created_at              TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at              TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (instance_id) REFERENCES ultimate_backend_instances(id) ON DELETE CASCADE,
                UNIQUE(instance_id, provider_name)
            )
        """)
        logger.info("Created table: ultimate_providers")

        # 3. Ultimate channels
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ultimate_channels (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ultimate_provider_id INTEGER NOT NULL,
                ultimate_channel_id TEXT    NOT NULL,
                channel_name        TEXT    NOT NULL,
                channel_number      INTEGER DEFAULT 0,
                logo_url            TEXT,
                catchup_hours       INTEGER DEFAULT 0,
                live_id             INTEGER,
                stream_uid          TEXT,
                enabled             INTEGER NOT NULL DEFAULT 1,
                created_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ultimate_provider_id) REFERENCES ultimate_providers(id) ON DELETE CASCADE,
                UNIQUE(ultimate_provider_id, ultimate_channel_id)
            )
        """)
        logger.info("Created table: ultimate_channels")

        # 4. Ultimate channel mappings (to logical channels)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ultimate_channel_mappings (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ultimate_channel_id INTEGER NOT NULL,
                channel_id          INTEGER NOT NULL,
                created_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ultimate_channel_id) REFERENCES ultimate_channels(id) ON DELETE CASCADE,
                FOREIGN KEY (channel_id)          REFERENCES channels(id)          ON DELETE CASCADE,
                UNIQUE(ultimate_channel_id, channel_id)
            )
        """)
        logger.info("Created table: ultimate_channel_mappings")

        # 5. Channel import state
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channel_import_state (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ultimate_channel_id INTEGER NOT NULL,
                last_imported_until TEXT,
                earliest_available  TEXT,
                latest_available    TEXT,
                last_successful_sync TEXT,
                sync_status         TEXT    NOT NULL DEFAULT 'pending',
                program_count       INTEGER NOT NULL DEFAULT 0,
                created_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ultimate_channel_id) REFERENCES ultimate_channels(id) ON DELETE CASCADE,
                UNIQUE(ultimate_channel_id)
            )
        """)
        logger.info("Created table: channel_import_state")

        # 6. Import batches
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS import_batches (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ultimate_channel_id INTEGER NOT NULL,
                batch_start         TEXT    NOT NULL,
                batch_end           TEXT    NOT NULL,
                programs_fetched    INTEGER NOT NULL DEFAULT 0,
                programs_inserted   INTEGER NOT NULL DEFAULT 0,
                programs_updated    INTEGER NOT NULL DEFAULT 0,
                programs_skipped    INTEGER NOT NULL DEFAULT 0,
                duration_ms         INTEGER,
                status              TEXT    NOT NULL DEFAULT 'pending',
                error_message       TEXT,
                created_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ultimate_channel_id) REFERENCES ultimate_channels(id) ON DELETE CASCADE
            )
        """)
        logger.info("Created table: import_batches")

        # ======================================================================
        # Extend the programs table
        #
        # Column mapping: UltimateBackendProgram → EPGEntry → DB column
        # ---------------------------------------------------------------
        # epg_id          → (stored as FK ref)  → ultimate_epg_id      INTEGER
        # schedule_id     → (internal)           → schedule_id          TEXT
        # genre (str)     → genre_description    → genre_description    TEXT
        #                   genre (DVB int)      → genre_dvb            INTEGER  ← new; populated by mapper
        # categories      → (extra metadata)     → categories           TEXT (JSON)
        # season_num      → season_number        → season_num           INTEGER
        # episode_num     → episode_number       → episode_num          INTEGER
        # has_episode_info→ flags (IS_SERIES)    → has_episode_info     INTEGER
        # director (str)  → directors ([str])    → directors            TEXT (JSON) -- was TEXT scalar
        # producer (str)  → (no EPGEntry field)  → producer             TEXT
        # year (int)      → year                 → year                 INTEGER  ← was production_year TEXT
        # rating (int)    → star_rating          → star_rating          INTEGER  ← was rating TEXT
        # thumbnail       → icon                 → thumbnail_url        TEXT
        # images          → (extra metadata)     → images               TEXT (JSON)
        # original_title  → original_title       → original_title       TEXT     ← new
        # epg_flags       → flags                → epg_flags            INTEGER  ← new; IS_SERIES etc.
        # ======================================================================

        cursor.execute("PRAGMA table_info(programs)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        new_columns = [
            # Ultimate Backend linkage
            ("ultimate_program_id", "TEXT"),
            ("ultimate_epg_id", "INTEGER"),
            ("schedule_id", "TEXT"),
            # Genre — store raw API string as description; numeric DVB code separate
            ("genre_description", "TEXT"),
            ("genre_dvb", "INTEGER"),  # EPGGenre.* constant, NULL until mapped
            # Categories (JSON array from API)
            ("categories", "TEXT"),
            # Episode info
            ("season_num", "INTEGER"),
            ("episode_num", "INTEGER"),
            ("has_episode_info", "INTEGER DEFAULT 0"),
            # People — directors stored as JSON array (API gives single string → wrapped)
            ("director", "TEXT"),  # keep raw scalar from API for reference
            ("producer", "TEXT"),
            # Year as INTEGER (was production_year TEXT in some earlier schemas)
            ("year", "INTEGER"),
            # Rating as INTEGER star_rating (0-10 scale matching EPGEntry.star_rating)
            ("star_rating", "INTEGER"),
            # Media
            ("thumbnail_url", "TEXT"),
            ("images", "TEXT"),
            # EPGEntry fields not previously stored
            ("original_title", "TEXT"),
            ("epg_flags", "INTEGER DEFAULT 0"),
        ]

        for col_name, col_def in new_columns:
            if col_name not in existing_columns:
                cursor.execute(f"ALTER TABLE programs ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added column '{col_name}' to programs table")
            else:
                logger.debug(f"Column '{col_name}' already exists, skipping")

        # ======================================================================
        # Indexes
        # ======================================================================

        indexes = [
            (
                "idx_programs_ultimate_epg_id",
                "CREATE INDEX IF NOT EXISTS idx_programs_ultimate_epg_id ON programs(ultimate_epg_id)",
            ),
            (
                "idx_programs_original_title",
                "CREATE INDEX IF NOT EXISTS idx_programs_original_title ON programs(channel_id, original_title)",
            ),
            (
                "idx_channel_import_state_status",
                "CREATE INDEX IF NOT EXISTS idx_channel_import_state_status ON channel_import_state(sync_status)",
            ),
            (
                "idx_import_batches_channel",
                "CREATE INDEX IF NOT EXISTS idx_import_batches_channel ON import_batches(ultimate_channel_id)",
            ),
            (
                "idx_ultimate_channels_provider",
                "CREATE INDEX IF NOT EXISTS idx_ultimate_channels_provider ON ultimate_channels(ultimate_provider_id)",
            ),
            (
                "idx_ultimate_mappings_channel",
                "CREATE INDEX IF NOT EXISTS idx_ultimate_mappings_channel ON ultimate_channel_mappings(channel_id)",
            ),
        ]

        for idx_name, idx_sql in indexes:
            cursor.execute(idx_sql)
            logger.debug(f"Ensured index: {idx_name}")

        # ======================================================================
        # Schema version bump
        # ======================================================================

        cursor.execute("UPDATE schema_version SET version = 3 WHERE version = 2")
        if cursor.rowcount == 0:
            cursor.execute("INSERT INTO schema_version (version) VALUES (3)")

        conn.commit()
        logger.info("Migration v2 → v3 completed successfully")

    except Exception as e:
        conn.rollback()
        logger.error(f"Migration v2 → v3 failed: {e}")
        raise
    finally:
        cursor.execute("PRAGMA foreign_keys = ON")
        if _close_when_done:
            conn.close()


def verify_v3_schema(db_path: str) -> bool:
    """Verify that all v3 schema elements exist."""
    required_tables = [
        "ultimate_backend_instances",
        "ultimate_providers",
        "ultimate_channels",
        "ultimate_channel_mappings",
        "channel_import_state",
        "import_batches",
    ]

    required_columns = [
        ("programs", "ultimate_epg_id"),
        ("programs", "schedule_id"),
        ("programs", "genre_description"),
        ("programs", "genre_dvb"),
        ("programs", "season_num"),
        ("programs", "episode_num"),
        ("programs", "has_episode_info"),
        ("programs", "original_title"),
        ("programs", "epg_flags"),
        ("programs", "year"),
        ("programs", "star_rating"),
        ("programs", "thumbnail_url"),
    ]

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}

        for table in required_tables:
            if table not in existing_tables:
                logger.error(f"Missing table: {table}")
                return False

        for table, column in required_columns:
            cursor.execute(f"PRAGMA table_info({table})")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if column not in existing_cols:
                logger.error(f"Missing column '{column}' in table '{table}'")
                return False

        logger.info("v3 schema verification passed")
        return True

    except Exception as e:
        logger.error(f"Schema verification failed: {e}")
        return False
    finally:
        conn.close()
