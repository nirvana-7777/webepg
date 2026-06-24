"""
Shared helpers for Ultimate Backend endpoints.
"""

import threading
from ...database.connection import get_db

# Per-provider in-flight guard so a bulk provider import can't be triggered
# twice concurrently (e.g. an impatient double-click or a retried request).
_ultimate_provider_imports_in_progress: set[str] = set()
_ultimate_provider_imports_lock = threading.Lock()


def resolve_ultimate_provider(identifier: str) -> dict | None:
    """
    Resolve an Ultimate Backend provider by numeric ultimate_providers.id
    or by provider_name. Returns a plain dict, or None if not found.
    """
    db = get_db()

    if identifier.isdigit():
        row = db.fetchone(
            """
            SELECT id, provider_name, provider_label, has_epg, enabled,
                   last_discovered_at, last_successful_import, error_count
            FROM ultimate_providers
            WHERE id = ?
            """,
            (int(identifier),),
        )
    else:
        row = db.fetchone(
            """
            SELECT id, provider_name, provider_label, has_epg, enabled,
                   last_discovered_at, last_successful_import, error_count
            FROM ultimate_providers
            WHERE provider_name = ?
            """,
            (identifier,),
        )

    if not row:
        return None

    return {
        "id": row[0],
        "provider_name": row[1],
        "provider_label": row[2],
        "has_epg": bool(row[3]),
        "enabled": bool(row[4]),
        "last_discovered_at": row[5],
        "last_successful_import": row[6],
        "error_count": row[7],
    }


def fetch_importable_channels(provider_id: int) -> list[tuple]:
    """
    Enabled channels for a provider that have a logical-channel mapping.

    Returns rows of (ultimate_channel_db_id, ultimate_channel_id,
    channel_name, logical_channel_id) - the exact shape
    incremental_import_channel() expects.
    """
    db = get_db()
    return db.fetchall(
        """
        SELECT uc.id, uc.ultimate_channel_id, uc.channel_name, ucm.channel_id
        FROM ultimate_channels uc
        JOIN ultimate_channel_mappings ucm ON ucm.ultimate_channel_id = uc.id
        WHERE uc.ultimate_provider_id = ? AND uc.enabled = 1
        """,
        (provider_id,),
    )


def resolve_ultimate_channel(provider_id: int, channel_identifier: str):
    """
    Resolve a single channel scoped to one provider, by numeric
    ultimate_channels.id or by the provider's own ultimate_channel_id string.
    """
    db = get_db()

    if channel_identifier.isdigit():
        return db.fetchone(
            """
            SELECT uc.id, uc.ultimate_channel_id, uc.channel_name, ucm.channel_id
            FROM ultimate_channels uc
            JOIN ultimate_channel_mappings ucm ON ucm.ultimate_channel_id = uc.id
            WHERE uc.ultimate_provider_id = ? AND uc.id = ? AND uc.enabled = 1
            """,
            (provider_id, int(channel_identifier)),
        )

    return db.fetchone(
        """
        SELECT uc.id, uc.ultimate_channel_id, uc.channel_name, ucm.channel_id
        FROM ultimate_channels uc
        JOIN ultimate_channel_mappings ucm ON ucm.ultimate_channel_id = uc.id
        WHERE uc.ultimate_provider_id = ? AND uc.ultimate_channel_id = ? AND uc.enabled = 1
        """,
        (provider_id, channel_identifier),
    )


# Expose the locks and in-progress set for other modules
__all__ = [
    "resolve_ultimate_provider",
    "fetch_importable_channels",
    "resolve_ultimate_channel",
    "_ultimate_provider_imports_in_progress",
    "_ultimate_provider_imports_lock",
]