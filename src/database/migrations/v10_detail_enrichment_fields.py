"""
Migration v9 -> v10.

Adds columns populated by the Ultimate Backend detail-enrichment path
(`get_program_details` -> `_enrich_program`):

  series_id           TEXT     -- EPGProgramDetails.series_id
  is_adult            INTEGER  -- 0/1 from EPGProgramDetails.is_adult
  audio_languages     TEXT     -- JSON array of language codes
  subtitle_languages  TEXT     -- JSON array of language codes

`country` (TEXT) already exists from v1->v2; the enrichment path now
starts writing it (it previously stayed NULL).
"""

import logging

logger = logging.getLogger(__name__)


def migrate_v9_to_v10(conn):
    cursor = conn.cursor()

    new_columns = [
        ("series_id", "TEXT"),
        ("is_adult", "INTEGER"),
        ("audio_languages", "TEXT"),
        ("subtitle_languages", "TEXT"),
    ]

    cursor.execute("PRAGMA table_info(programs)")
    existing = {row[1] for row in cursor.fetchall()}

    for column_name, column_type in new_columns:
        if column_name not in existing:
            cursor.execute(
                f"ALTER TABLE programs ADD COLUMN {column_name} {column_type}"
            )
            logger.info(f"Added column {column_name} to programs table")
        else:
            logger.info(f"Column {column_name} already exists, skipping")

    conn.commit()
    logger.info("Migration to version 10 completed")