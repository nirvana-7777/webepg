# file: src/database/migrations.py
"""
Database migration script for new fields.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


def migrate_v1_to_v2(db_path: str):
    """
    Migrate database from schema version 1 to version 2.
    Adds new fields: presenters, writers, producers, production_year, country.
    Converts actors and directors from comma-separated strings to JSON arrays.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Add new columns if they don't exist
        cursor.execute("PRAGMA foreign_keys = OFF")

        # Check if columns already exist
        cursor.execute("PRAGMA table_info(programs)")
        columns = [col[1] for col in cursor.fetchall()]

        new_columns = [
            ("presenters", "TEXT"),
            ("writers", "TEXT"),
            ("producers", "TEXT"),
            ("production_year", "TEXT"),
            ("country", "TEXT"),
        ]

        for column_name, column_type in new_columns:
            if column_name not in columns:
                cursor.execute(
                    f"ALTER TABLE programs ADD COLUMN {column_name} {column_type}"
                )
                logger.info(f"Added column {column_name} to programs table")

        # Convert existing comma-separated actors/directors to JSON arrays
        import json

        # Update actors field
        cursor.execute(
            "SELECT id, actors FROM programs WHERE actors IS NOT NULL AND actors != ''"
        )
        rows = cursor.fetchall()

        for program_id, actors_str in rows:
            if actors_str and not actors_str.startswith("["):  # Not JSON yet
                # Convert comma-separated to JSON array
                actors_list = [
                    actor.strip() for actor in actors_str.split(",") if actor.strip()
                ]
                actors_json = json.dumps(actors_list)
                cursor.execute(
                    "UPDATE programs SET actors = ? WHERE id = ?",
                    (actors_json, program_id),
                )

        # Update directors field
        cursor.execute(
            "SELECT id, directors FROM programs WHERE directors IS NOT NULL AND directors != ''"
        )
        rows = cursor.fetchall()

        for program_id, directors_str in rows:
            if directors_str and not directors_str.startswith("["):  # Not JSON yet
                # Convert comma-separated to JSON array
                directors_list = [
                    director.strip()
                    for director in directors_str.split(",")
                    if director.strip()
                ]
                directors_json = json.dumps(directors_list)
                cursor.execute(
                    "UPDATE programs SET directors = ? WHERE id = ?",
                    (directors_json, program_id),
                )

        # Update schema version
        cursor.execute("UPDATE schema_version SET version = 2 WHERE version = 1")

        conn.commit()
        logger.info("Migration to version 2 completed successfully")

    except Exception as e:
        conn.rollback()
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        cursor.execute("PRAGMA foreign_keys = ON")
        conn.close()
