"""
Ultimate Backend provider endpoints.
"""

import logging
import threading
import asyncio
from flask import Blueprint, jsonify

from .helpers import (
    resolve_ultimate_provider,
    fetch_importable_channels,
    _ultimate_provider_imports_in_progress,
    _ultimate_provider_imports_lock,
)
from .. import ServiceRegistry

logger = logging.getLogger(__name__)

ultimate_providers_bp = Blueprint("ultimate_providers", __name__)


@ultimate_providers_bp.route("/ultimate/providers/<identifier>/import", methods=["POST"])
def trigger_ultimate_provider_import(identifier):
    """
    Trigger an incremental import for every enabled, mapped channel
    belonging to one Ultimate Backend provider.

    `identifier` may be the numeric ultimate_providers.id or its
    provider_name (e.g. "magenta2").
    """
    scheduler = ServiceRegistry.scheduler
    ultimate_import_service = getattr(scheduler, "ultimate_import_service", None)
    if not scheduler or not ultimate_import_service:
        return jsonify({"error": "Ultimate Backend not initialized"}), 500

    try:
        provider = resolve_ultimate_provider(identifier)
        if not provider:
            return jsonify({"error": f"Ultimate Backend provider not found: {identifier}"}), 404

        if not provider["enabled"]:
            return jsonify({"error": f"Provider '{provider['provider_name']}' is disabled"}), 400

        channels = fetch_importable_channels(provider["id"])
        if not channels:
            return jsonify(
                {"error": f"No enabled, mapped channels found for provider '{provider['provider_name']}'"}
            ), 404

        provider_name = provider["provider_name"]

        with _ultimate_provider_imports_lock:
            if provider_name in _ultimate_provider_imports_in_progress:
                return jsonify(
                    {"error": f"Import already in progress for provider '{provider_name}'"}
                ), 409
            _ultimate_provider_imports_in_progress.add(provider_name)

        def run_provider_import():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            failures = []
            try:
                for db_id, ultimate_channel_id, channel_name, logical_channel_id in channels:
                    try:
                        loop.run_until_complete(
                            ultimate_import_service.incremental_import_channel(
                                ultimate_channel_db_id=db_id,
                                provider_name=provider_name,
                                ultimate_channel_id=ultimate_channel_id,
                                logical_channel_id=logical_channel_id,
                                channel_name=channel_name,
                            )
                        )
                    except Exception as exc:
                        failures.append(channel_name)
                        logger.error(
                            f"Failed to import channel '{channel_name}' "
                            f"for provider '{provider_name}': {exc}"
                        )
                if failures:
                    logger.warning(
                        f"Provider import for '{provider_name}' finished with "
                        f"{len(failures)}/{len(channels)} channel failures: {failures}"
                    )
                else:
                    logger.info(
                        f"Provider import for '{provider_name}' completed: "
                        f"{len(channels)} channels"
                    )
            finally:
                loop.close()
                with _ultimate_provider_imports_lock:
                    _ultimate_provider_imports_in_progress.discard(provider_name)

        threading.Thread(target=run_provider_import, daemon=True).start()

        return jsonify(
            {
                "success": True,
                "message": f"Import triggered for Ultimate Backend provider: '{provider_name}'",
                "provider_id": provider["id"],
                "provider_name": provider_name,
                "channels_queued": len(channels),
            }
        ), 202

    except Exception as e:
        logger.error(f"Error triggering Ultimate Backend provider import for {identifier}: {e}")
        return jsonify({"error": str(e)}), 500

@ultimate_providers_bp.route("/ultimate/providers/<identifier>/grid-import", methods=["POST"])
def trigger_ultimate_provider_grid_import(identifier):
    scheduler = ServiceRegistry.scheduler
    grid_service = getattr(scheduler, "grid_import_service", None)
    if not scheduler or not grid_service:
        return jsonify({"error": "Ultimate Backend grid import not initialized"}), 500

    provider = resolve_ultimate_provider(identifier)
    if not provider:
        return jsonify({"error": f"Provider not found: {identifier}"}), 404

    lock_key = f"grid:{provider['provider_name']}"
    with _ultimate_provider_imports_lock:
        if lock_key in _ultimate_provider_imports_in_progress:
            return jsonify({"error": "Grid import already in progress"}), 409
        _ultimate_provider_imports_in_progress.add(lock_key)

    def run_grid_import():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(grid_service.grid_import_provider(provider["provider_name"]))
        finally:
            loop.close()
            with _ultimate_provider_imports_lock:
                _ultimate_provider_imports_in_progress.discard(lock_key)

    threading.Thread(target=run_grid_import, daemon=True).start()
    return jsonify({"message": f"Grid import triggered for '{provider['provider_name']}'"}), 202

@ultimate_providers_bp.route("/ultimate/providers/<identifier>/status", methods=["GET"])
def get_ultimate_provider_status(identifier):
    """Sync/import status for a single Ultimate Backend provider."""
    try:
        provider = resolve_ultimate_provider(identifier)
        if not provider:
            return jsonify({"error": f"Provider not found: {identifier}"}), 404

        from ...database.connection import get_db

        db = get_db()
        counts = db.fetchone(
            """
            SELECT
                COUNT(DISTINCT uc.id) AS total_channels,
                COUNT(DISTINCT ucm.ultimate_channel_id) AS mapped_channels,
                SUM(CASE WHEN cis.sync_status = 'success' THEN 1 ELSE 0 END) AS synced_channels,
                SUM(CASE WHEN cis.sync_status = 'failed' THEN 1 ELSE 0 END) AS failed_channels,
                SUM(CASE WHEN cis.sync_status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress_channels,
                SUM(CASE WHEN cis.sync_status = 'pending' THEN 1 ELSE 0 END) AS pending_channels
            FROM ultimate_channels uc
            LEFT JOIN ultimate_channel_mappings ucm ON ucm.ultimate_channel_id = uc.id
            LEFT JOIN channel_import_state cis ON cis.ultimate_channel_id = uc.id
            WHERE uc.ultimate_provider_id = ? AND uc.enabled = 1
            """,
            (provider["id"],),
        )

        with _ultimate_provider_imports_lock:
            in_flight = provider["provider_name"] in _ultimate_provider_imports_in_progress

        return jsonify(
            {
                "id": provider["id"],
                "name": provider["provider_name"],
                "label": provider["provider_label"],
                "enabled": provider["enabled"],
                "has_epg": provider["has_epg"],
                "last_discovered_at": provider["last_discovered_at"],
                "last_successful_import": provider["last_successful_import"],
                "error_count": provider["error_count"],
                "import_in_progress": in_flight,
                "total_channels": counts[0] if counts else 0,
                "mapped_channels": counts[1] if counts else 0,
                "synced_channels": counts[2] or 0 if counts else 0,
                "failed_channels": counts[3] or 0 if counts else 0,
                "in_progress_channels": counts[4] or 0 if counts else 0,
                "pending_channels": counts[5] or 0 if counts else 0,
            }
        )
    except Exception as e:
        logger.error(f"Error getting provider status for {identifier}: {e}")
        return jsonify({"error": str(e)}), 500


@ultimate_providers_bp.route("/ultimate/providers/status", methods=["GET"])
def list_ultimate_providers_status():
    """
    Per-provider sync status breakdown for all Ultimate Backend providers.

    Complements GET /ultimate/status, which reports instance-wide totals;
    this returns one row per provider in a single aggregate query (no N+1).
    """
    try:
        from ...database.connection import get_db

        db = get_db()
        rows = db.fetchall(
            """
            SELECT
                up.id,
                up.provider_name,
                up.provider_label,
                up.enabled,
                up.has_epg,
                up.last_discovered_at,
                up.last_successful_import,
                up.error_count,
                COUNT(DISTINCT uc.id) AS total_channels,
                SUM(CASE WHEN cis.sync_status = 'success' THEN 1 ELSE 0 END) AS synced_channels,
                SUM(CASE WHEN cis.sync_status = 'failed' THEN 1 ELSE 0 END) AS failed_channels,
                SUM(CASE WHEN cis.sync_status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress_channels,
                SUM(CASE WHEN cis.sync_status = 'pending' THEN 1 ELSE 0 END) AS pending_channels
            FROM ultimate_providers up
            LEFT JOIN ultimate_channels uc ON uc.ultimate_provider_id = up.id AND uc.enabled = 1
            LEFT JOIN channel_import_state cis ON cis.ultimate_channel_id = uc.id
            GROUP BY up.id
            ORDER BY up.provider_name
            """
        )

        with _ultimate_provider_imports_lock:
            in_flight_set = _ultimate_provider_imports_in_progress.copy()

        return jsonify(
            {
                "providers": [
                    {
                        "id": r[0],
                        "name": r[1],
                        "label": r[2],
                        "enabled": bool(r[3]),
                        "has_epg": bool(r[4]),
                        "last_discovered_at": r[5],
                        "last_successful_import": r[6],
                        "error_count": r[7],
                        "import_in_progress": r[1] in in_flight_set,
                        "total_channels": r[8],
                        "synced_channels": r[9] or 0,
                        "failed_channels": r[10] or 0,
                        "in_progress_channels": r[11] or 0,
                        "pending_channels": r[12] or 0,
                    }
                    for r in rows
                ]
            }
        )
    except Exception as e:
        logger.error(f"Error listing ultimate provider status: {e}")
        return jsonify({"error": str(e)}), 500