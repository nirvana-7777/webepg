"""
Database migration: Add language column to programs table.
Version 5 → 6
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


def migrate_v5_to_v6(db_or_path):
    """Add language column to programs table."""
    if isinstance(db_or_path, str):
        conn = sqlite3.connect(db_or_path)
        _close_when_done = True
    else:
        conn = db_or_path
        _close_when_done = False

    cursor = conn.cursor()

    try:
        # Check if column already exists
        cursor.execute("PRAGMA table_info(programs)")
        columns = [row[1] for row in cursor.fetchall()]

        if "language" not in columns:
            logger.info("Adding 'language' column to programs table")
            cursor.execute("ALTER TABLE programs ADD COLUMN language TEXT")
        else:
            logger.info("'language' column already exists in programs table")

        # Update schema version
        cursor.execute("UPDATE schema_version SET version = 6 WHERE version = 5")
        if cursor.rowcount == 0:
            cursor.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (6)")

        conn.commit()
        logger.info("Migration v5 → v6 completed successfully")

    except Exception as e:
        conn.rollback()
        logger.error(f"Migration v5 → v6 failed: {e}")
        raise
    finally:
        if _close_when_done:
            conn.close()
