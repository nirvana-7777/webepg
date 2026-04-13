# src/database/migrations/v4_unified_providers.py
"""
Migration v3 → v4: Unified provider schema supporting both XMLTV and Ultimate Backend.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


def migrate_v3_to_v4(db_or_path):
    """Migrate to unified provider schema."""
    if isinstance(db_or_path, str):
        conn = sqlite3.connect(db_or_path)
        close_when_done = True
    else:
        conn = db_or_path
        close_when_done = False

    cursor = conn.cursor()

    try:
        cursor.execute("PRAGMA foreign_keys = OFF")

        # ======================================================================
        # Step 1: Create new providers table with expanded schema
        # ======================================================================

        cursor.execute("""
            CREATE TABLE providers_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                -- Core identification
                name TEXT NOT NULL UNIQUE,
                display_name TEXT,
                source_type TEXT NOT NULL DEFAULT 'xmltv',  -- 'xmltv' or 'ultimate_backend'

                -- XMLTV specific (NULL for Ultimate Backend)
                xmltv_url TEXT,

                -- Ultimate Backend specific (NULL for XMLTV)
                ultimate_instance_id INTEGER,
                ultimate_provider_name TEXT,
                plugin_name TEXT,
                country TEXT,
                logo_url TEXT,

                -- Auth configuration (JSON)
                auth_config TEXT,  -- JSON object with auth settings

                -- Provider capabilities
                has_epg BOOLEAN DEFAULT 1,
                requires_credentials BOOLEAN DEFAULT 0,

                -- Status
                enabled BOOLEAN DEFAULT 1,
                instance_ready BOOLEAN DEFAULT 1,

                -- Timestamps
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                -- Foreign keys
                FOREIGN KEY (ultimate_instance_id) REFERENCES ultimate_backend_instances(id) ON DELETE SET NULL
            )
        """)

        # ======================================================================
        # Step 2: Migrate existing XMLTV providers
        # ======================================================================

        cursor.execute("""
            INSERT INTO providers_new (
                id, name, xmltv_url, enabled, source_type, created_at, updated_at
            )
            SELECT 
                id, name, xmltv_url, enabled, 'xmltv', created_at, updated_at
            FROM providers
        """)

        # ======================================================================
        # Step 3: Migrate Ultimate Backend providers from ultimate_providers
        # ======================================================================

        # First, check if ultimate_providers table exists
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='ultimate_providers'
        """)

        if cursor.fetchone():
            # Migrate ultimate providers
            cursor.execute("""
                INSERT INTO providers_new (
                    name, 
                    display_name,
                    source_type,
                    ultimate_instance_id,
                    ultimate_provider_name,
                    plugin_name,
                    country,
                    logo_url,
                    has_epg,
                    requires_credentials,
                    enabled,
                    instance_ready,
                    created_at,
                    updated_at
                )
                SELECT 
                    up.provider_name,
                    up.provider_label,
                    'ultimate_backend',
                    up.instance_id,
                    up.provider_name,
                    NULL,  -- plugin_name will be populated from API data later
                    NULL,  -- country will be populated from API data later
                    NULL,  -- logo_url will be populated from API data later
                    up.has_epg,
                    0,     -- requires_credentials (will be updated from API)
                    up.enabled,
                    1,     -- instance_ready
                    up.created_at,
                    up.updated_at
                FROM ultimate_providers up
            """)

        # ======================================================================
        # Step 4: Create indexes
        # ======================================================================

        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_providers_source_type ON providers_new(source_type)",
            "CREATE INDEX IF NOT EXISTS idx_providers_ultimate_instance ON providers_new(ultimate_instance_id)",
            "CREATE INDEX IF NOT EXISTS idx_providers_country ON providers_new(country)",
            "CREATE INDEX IF NOT EXISTS idx_providers_enabled ON providers_new(enabled)",
        ]

        for idx_sql in indexes:
            cursor.execute(idx_sql)

        # ======================================================================
        # Step 5: Replace old table and update foreign keys
        # ======================================================================

        # Drop old table and rename new one
        cursor.execute("DROP TABLE providers")
        cursor.execute("ALTER TABLE providers_new RENAME TO providers")

        # ======================================================================
        # Step 6: Create provider_auth table for credential management
        # ======================================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS provider_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER NOT NULL,
                credential_type TEXT NOT NULL,  -- 'user_credentials', 'client_credentials', 'api_key'
                username TEXT,
                password TEXT,
                client_id TEXT,
                client_secret TEXT,
                api_key TEXT,
                token_data TEXT,  -- JSON for storing OAuth tokens
                expires_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE,
                UNIQUE(provider_id, credential_type)
            )
        """)

        # ======================================================================
        # Step 7: Create provider_epg_config for per-provider EPG settings
        # ======================================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS provider_epg_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER NOT NULL,

                -- EPG fetch settings
                future_days INTEGER DEFAULT 7,
                past_days INTEGER DEFAULT 7,
                chunk_hours INTEGER DEFAULT 24,

                -- Rate limiting
                max_requests_per_second REAL DEFAULT 5.0,
                max_concurrent_channels INTEGER DEFAULT 3,

                -- Retry settings
                max_retries INTEGER DEFAULT 3,
                timeout_seconds INTEGER DEFAULT 30,

                -- Custom headers (JSON)
                custom_headers TEXT,

                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE,
                UNIQUE(provider_id)
            )
        """)

        # ======================================================================
        # Step 8: Create view for unified provider access
        # ======================================================================

        cursor.execute("""
            CREATE VIEW IF NOT EXISTS v_all_providers AS
            SELECT 
                p.*,
                CASE 
                    WHEN p.source_type = 'xmltv' THEN p.xmltv_url
                    WHEN p.source_type = 'ultimate_backend' THEN ubi.base_url
                END as source_url,
                CASE 
                    WHEN p.source_type = 'ultimate_backend' THEN pecfg.future_days
                    ELSE NULL
                END as epg_future_days,
                CASE 
                    WHEN p.source_type = 'ultimate_backend' THEN pecfg.past_days
                    ELSE NULL
                END as epg_past_days
            FROM providers p
            LEFT JOIN ultimate_backend_instances ubi ON p.ultimate_instance_id = ubi.id
            LEFT JOIN provider_epg_config pecfg ON p.id = pecfg.provider_id
        """)

        # ======================================================================
        # Step 9: Update schema version
        # ======================================================================

        cursor.execute("UPDATE schema_version SET version = 4 WHERE version = 3")
        if cursor.rowcount == 0:
            cursor.execute("INSERT INTO schema_version (version) VALUES (4)")

        cursor.execute("PRAGMA foreign_keys = ON")
        conn.commit()

        logger.info("Migration v3 → v4 completed: unified provider schema")

    except Exception as e:
        conn.rollback()
        logger.error(f"Migration v4 failed: {e}")
        raise
    finally:
        if close_when_done:
            conn.close()