"""
Migration v7 -> v8: add error-tracking column to channel_import_state.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


def migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """Add last_error column to channel_import_state table."""
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(channel_import_state)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    if "last_error" not in existing_columns:
        cursor.execute(
            "ALTER TABLE channel_import_state ADD COLUMN last_error TEXT"
        )
        logger.info("Added column 'last_error' to channel_import_state table")
    else:
        logger.debug("Column 'last_error' already exists, skipping")

    conn.commit()
    logger.info("Migration v7 -> v8 completed: added last_error tracking column")