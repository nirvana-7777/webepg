"""
Migration v10 -> v11.

Adds the ``persons`` table: one row per (provider, person id) holding the
person's image and roles, fetched once from the Ultimate Backend
(``GET /api/providers/<provider>/persons/<id>``).

The table deliberately has no foreign key to ``programs``: programme
retention cleanup and de-duplication never touch it, so a person is fetched
once and kept. Programmes reference persons by id inside their JSON credit
lists (``programs.actors`` etc.).

status:
    pending    -- registered, image/roles not fetched yet
    ok         -- fetched (image_url may still be NULL if upstream has none)
    not_found  -- backend answered 404; not retried automatically

Backfill: programmes enriched before this migration already carry
``{"id", "name", ...}`` dicts in their credit columns (and are never
re-enriched), so their persons are registered here.
"""

import json
import logging

logger = logging.getLogger(__name__)

_CREDIT_COLUMNS = ("actors", "directors", "writers", "producers", "presenters")
_FETCH_SIZE = 2000


def migrate_v10_to_v11(conn):
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS persons (
            provider_id    INTEGER NOT NULL,
            person_id      TEXT NOT NULL,
            name           TEXT NOT NULL,
            image_url      TEXT,
            roles          TEXT,
            status         TEXT NOT NULL DEFAULT 'pending',
            fetch_attempts INTEGER NOT NULL DEFAULT 0,
            fetched_at     TEXT,
            created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (provider_id, person_id),
            FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_persons_status "
        "ON persons(provider_id, status, fetch_attempts)"
    )
    conn.commit()

    registered = _backfill_from_programs(conn)
    logger.info(f"Migration to version 11 completed ({registered} persons backfilled)")


def _backfill_from_programs(conn) -> int:
    where = " OR ".join(f"{col} LIKE '%\"id\"%'" for col in _CREDIT_COLUMNS)
    read = conn.cursor()
    read.execute(
        f"SELECT provider_id, {', '.join(_CREDIT_COLUMNS)} "
        f"FROM programs WHERE {where}"
    )

    total = 0
    while True:
        rows = read.fetchmany(_FETCH_SIZE)
        if not rows:
            break

        found = {}
        for row in rows:
            provider_id = row[0]
            for raw in row[1:]:
                if not raw or '"id"' not in raw:
                    continue
                try:
                    entries = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("id") and entry.get("name"):
                        found.setdefault(
                            (provider_id, str(entry["id"])), str(entry["name"])
                        )

        if found:
            conn.executemany(
                "INSERT OR IGNORE INTO persons (provider_id, person_id, name) "
                "VALUES (?, ?, ?)",
                [(pid, person_id, name) for (pid, person_id), name in found.items()],
            )
            conn.commit()
            total += len(found)

    return total
