"""
Database migration: Add epg_event_id column to programs table.
Version 8 → 9

epg_event_id is the provider-native LISTING guid (distinct from
schedule_id/program_id, which identifies the underlying content).
It is required to schedule an nPVR recording for a given broadcast.
See clients/models.py:UltimateBackendProgram and epg_models.py:EPGEntry
for the field's full semantics.

NOTE: this migration only adds storage for the field. It does NOT
backfill it — existing rows will have epg_event_id = NULL, which is
expected and safe (see to_dict()/from_db_row() handling in models.py).
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


def migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    """
    Add epg_event_id column to programs table.

    Idempotent: safe to call against a database that already has the
    column (e.g. a retried migration after a partial failure).

    Note: unlike initialize_database(), this does not manage its own
    connection or update schema_version — both are handled by
    SchemaManager._migrate_database()/_update_schema_version(), which
    calls this function with an already-open connection and commits/
    rolls back around it.
    """
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(programs)")
    columns = [row[1] for row in cursor.fetchall()]

    if "epg_event_id" not in columns:
        logger.info("Adding 'epg_event_id' column to programs table")
        cursor.execute("ALTER TABLE programs ADD COLUMN epg_event_id TEXT")
    else:
        logger.info("'epg_event_id' column already exists in programs table, skipping")

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_programs_epg_event_id ON programs(epg_event_id)"
    )

    conn.commit()
    logger.info("Migration v8 → v9 completed successfully")