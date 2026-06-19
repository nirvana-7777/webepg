"""
Migration v6 -> v7: add tracking columns for grid + detail-enrichment import.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


def migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """Add has_details / enrichment tracking columns to programs table."""
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(programs)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    new_columns = [
        ("has_details", "INTEGER DEFAULT 0"),
        ("grid_fetched_at", "TEXT"),
        ("enriched_at", "TEXT"),
        ("detail_fetch_attempts", "INTEGER DEFAULT 0"),
        ("import_source", "TEXT DEFAULT 'xmltv'"),
    ]

    for col_name, col_def in new_columns:
        if col_name not in existing_columns:
            cursor.execute(f"ALTER TABLE programs ADD COLUMN {col_name} {col_def}")
            logger.info(f"Added column '{col_name}' to programs table")
        else:
            logger.debug(f"Column '{col_name}' already exists, skipping")

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_programs_has_details ON programs(has_details)",
        "CREATE INDEX IF NOT EXISTS idx_programs_start_time_details ON programs(start_time, has_details)",
        "CREATE INDEX IF NOT EXISTS idx_programs_import_source ON programs(import_source)",
    ]
    for idx_sql in indexes:
        cursor.execute(idx_sql)

    conn.commit()
    logger.info("Migration v6 -> v7 completed: added grid/detail tracking columns")
