"""
Database migration to add updated_at column to channels table.
Version 4 → 5
"""

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def migrate_v4_to_v5(db_or_path):
    """Add updated_at column to channels table."""
    if isinstance(db_or_path, str):
        conn = sqlite3.connect(db_or_path)
        close_when_done = True
    else:
        conn = db_or_path
        close_when_done = False

    cursor = conn.cursor()

    try:
        cursor.execute("PRAGMA foreign_keys = OFF")

        # Check if updated_at column exists in channels table
        cursor.execute("PRAGMA table_info(channels)")
        columns = {row[1] for row in cursor.fetchall()}

        if "updated_at" not in columns:
            # Add column without default first (NULL allowed)
            cursor.execute("ALTER TABLE channels ADD COLUMN updated_at TEXT")
            logger.info("Added updated_at column to channels table (NULLable)")

            # Then update existing rows with current timestamp
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute("UPDATE channels SET updated_at = ? WHERE updated_at IS NULL", (now,))
            logger.info(f"Set updated_at for {cursor.rowcount} existing rows")

            # Now add the default for future inserts
            # SQLite doesn't support ALTER COLUMN, so we need to recreate the table
            # or just handle defaults in code. For simplicity, we'll skip the DEFAULT
            # and handle it in the application code.
            logger.info("Note: updated_at default must be handled by application code")
        else:
            logger.info("updated_at column already exists in channels table")

        # Also check ultimate_channels table
        cursor.execute("PRAGMA table_info(ultimate_channels)")
        columns = {row[1] for row in cursor.fetchall()}

        if "updated_at" not in columns:
            cursor.execute("ALTER TABLE ultimate_channels ADD COLUMN updated_at TEXT")
            logger.info("Added updated_at column to ultimate_channels table (NULLable)")

            # Update existing rows
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute("UPDATE ultimate_channels SET updated_at = ? WHERE updated_at IS NULL", (now,))
            logger.info(f"Set updated_at for {cursor.rowcount} existing rows in ultimate_channels")
        else:
            logger.info("updated_at column already exists in ultimate_channels table")

        # Update schema version
        cursor.execute("UPDATE schema_version SET version = 5 WHERE version = 4")
        if cursor.rowcount == 0:
            cursor.execute("INSERT INTO schema_version (version) VALUES (5)")

        cursor.execute("PRAGMA foreign_keys = ON")
        conn.commit()

        logger.info("Migration v4 → v5 completed successfully")

    except Exception as e:
        conn.rollback()
        logger.error(f"Migration v5 failed: {e}")
        raise
    finally:
        if close_when_done:
            conn.close()