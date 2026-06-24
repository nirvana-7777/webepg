"""
Ultimate Backend operations endpoints (grid import, discovery, full/incremental imports).
"""

import logging
import threading
import asyncio
import inspect
from flask import Blueprint, jsonify, request

from .helpers import resolve_ultimate_provider
from .. import ServiceRegistry

logger = logging.getLogger(__name__)

ultimate_operations_bp = Blueprint("ultimate_operations", __name__)


@ultimate_operations_bp.route("/ultimate/grid/import", methods=["POST"])
def trigger_grid_import():
    """
    Manually trigger Ultimate Backend grid import.

    Optional ?provider=<id-or-name> scopes the import to one provider.

    VERIFY BEFORE RELYING ON SCOPED IMPORTS: this requires
    grid_import_service to expose a `grid_import_provider(provider_name)`
    coroutine. I could not confirm that method exists in the version of
    grid_import_service.py I reviewed - only the unscoped
    scheduler.trigger_grid_import_now() was confirmed. If the method is
    missing, this returns 501 instead of silently running the unscoped
    job under a provider-scoped URL.
    """
    scheduler = ServiceRegistry.scheduler
    grid_import_service = getattr(scheduler, "grid_import_service", None)
    if not scheduler or not grid_import_service:
        return jsonify({"error": "Grid import not enabled"}), 400

    provider_identifier = request.args.get("provider")

    try:
        if not provider_identifier:
            scheduler.trigger_grid_import_now()
            next_run = scheduler.get_next_run_time("daily_grid_import")
            return jsonify(
                {
                    "message": "Grid import triggered",
                    "next_scheduled": next_run.isoformat() if next_run else None,
                }
            )

        provider = resolve_ultimate_provider(provider_identifier)
        if not provider:
            return jsonify({"error": f"Provider not found: {provider_identifier}"}), 404

        if not hasattr(grid_import_service, "grid_import_provider"):
            return jsonify(
                {
                    "error": (
                        "grid_import_service has no per-provider grid_import_provider() "
                        "method yet - only the unscoped POST /ultimate/grid/import "
                        "(no ?provider= param) is currently supported"
                    )
                }
            ), 501

        def run_grid_import():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    grid_import_service.grid_import_provider(provider["provider_name"])
                )
                logger.info(f"Grid import for {provider['provider_name']}: {result}")
            except Exception as exc:
                logger.error(f"Grid import failed for {provider['provider_name']}: {exc}")
            finally:
                loop.close()

        threading.Thread(target=run_grid_import, daemon=True).start()

        return jsonify(
            {
                "success": True,
                "message": f"Grid import triggered for provider: '{provider['provider_name']}'",
                "provider_id": provider["id"],
                "provider_name": provider["provider_name"],
            }
        ), 202

    except Exception as e:
        logger.error(f"Error triggering grid import: {e}")
        return jsonify({"error": str(e)}), 500


@ultimate_operations_bp.route("/ultimate/discover", methods=["POST"])
def trigger_ultimate_discovery():
    """
    Manually trigger provider/channel discovery.

    Optional ?provider=<name> scopes discovery to one provider.

    VERIFY BEFORE RELYING ON SCOPED DISCOVERY: scheduler.trigger_ultimate_discovery_now()
    was only confirmed as a zero-argument call (UltimateBackendDiscoveryService.discover_all()
    has no provider filter either). If the scheduler's method doesn't accept a
    provider_name argument, this returns 501 rather than guessing and
    accidentally re-running full discovery under a "scoped" label.
    """
    scheduler = ServiceRegistry.scheduler
    if not scheduler:
        return jsonify({"error": "Scheduler not initialized"}), 500

    provider_name = request.args.get("provider")

    try:
        if provider_name:
            params = inspect.signature(scheduler.trigger_ultimate_discovery_now).parameters
            if not params:
                return jsonify(
                    {
                        "error": (
                            "Per-provider discovery is not supported: "
                            "trigger_ultimate_discovery_now() takes no arguments. "
                            "Use POST /ultimate/discover (no ?provider=) for full discovery."
                        )
                    }
                ), 501
            scheduler.trigger_ultimate_discovery_now(provider_name)
            message = f"Ultimate Backend discovery triggered for provider: {provider_name}"
        else:
            scheduler.trigger_ultimate_discovery_now()
            message = "Ultimate Backend discovery triggered"

        next_run = scheduler.get_next_run_time("weekly_ultimate_discovery")
        return jsonify(
            {
                "message": message,
                "provider": provider_name,
                "next_scheduled": (next_run.isoformat() if next_run else None),
            }
        )
    except Exception as e:
        logger.error(f"Error triggering discovery: {e}")
        return jsonify({"error": str(e)}), 500


@ultimate_operations_bp.route("/ultimate/import/full", methods=["POST"])
def trigger_ultimate_full_import():
    """
    Manually trigger a full Ultimate Backend import (bootstrap / re-sync).

    Resets all channel cursors and re-fetches past_days of history plus
    api_max_future_days of future data.  Runs in a daemon thread so the
    request returns immediately.
    """
    scheduler = ServiceRegistry.scheduler
    if not scheduler or not scheduler.ultimate_import_service:
        return jsonify({"error": "Ultimate Backend not initialized"}), 500

    try:
        scheduler.trigger_ultimate_full_now()
        return jsonify(
            {
                "message": "Full Ultimate Backend import triggered",
                "type": "full_import",
            }
        )
    except Exception as e:
        logger.error(f"Error triggering full import: {e}")
        return jsonify({"error": str(e)}), 500


@ultimate_operations_bp.route("/ultimate/import/incremental", methods=["POST"])
def trigger_ultimate_incremental_import():
    """
    Manually trigger an incremental Ultimate Backend import.

    Each channel advances from its last_imported_until cursor; channels with
    no prior state fall back to a full historical fetch.
    """
    scheduler = ServiceRegistry.scheduler
    if not scheduler or not scheduler.ultimate_import_service:
        return jsonify({"error": "Ultimate Backend not initialized"}), 500

    try:
        scheduler.trigger_ultimate_incremental_now()
        return jsonify(
            {
                "message": "Incremental Ultimate Backend import triggered",
                "type": "incremental_import",
            }
        )
    except Exception as e:
        logger.error(f"Error triggering incremental import: {e}")
        return jsonify({"error": str(e)}), 500


@ultimate_operations_bp.route("/ultimate/status", methods=["GET"])
def get_ultimate_status():
    """Get status of Ultimate Backend integration."""
    try:
        from ...database.connection import get_db

        db = get_db()

        # Get instance info
        instance = db.fetchone(
            "SELECT id, name, base_url, enabled FROM ultimate_backend_instances WHERE name = 'main'"
        )

        # Get provider stats
        providers = db.fetchall(
            """
            SELECT provider_name, provider_label, has_epg, enabled, 
                   last_discovered_at, last_successful_import, error_count
            FROM ultimate_providers
            WHERE instance_id = ?
        """,
            (instance[0] if instance else 0,),
        )

        # Get channel stats
        channels = db.fetchone("""
            SELECT COUNT(*) as total, 
                   SUM(CASE WHEN sync_status = 'success' THEN 1 ELSE 0 END) as synced,
                   SUM(CASE WHEN sync_status = 'failed' THEN 1 ELSE 0 END) as failed,
                   SUM(CASE WHEN sync_status = 'in_progress' THEN 1 ELSE 0 END) as in_progress
            FROM channel_import_state
        """)

        # Get recent import stats
        recent_imports = db.fetchall("""
            SELECT 
                datetime(batch_start) as start,
                datetime(batch_end) as end,
                programs_fetched,
                programs_inserted,
                programs_updated,
                status,
                datetime(created_at) as created_at
            FROM import_batches
            ORDER BY created_at DESC
            LIMIT 10
        """)

        return jsonify(
            {
                "instance": (
                    {
                        "name": instance[1] if instance else None,
                        "base_url": instance[2] if instance else None,
                        "enabled": bool(instance[3]) if instance else False,
                    }
                    if instance
                    else None
                ),
                "providers": {
                    "total": len(providers),
                    "with_epg": sum(1 for p in providers if p["has_epg"]),
                    "enabled": sum(1 for p in providers if p["enabled"]),
                    "list": [
                        {
                            "name": p["provider_name"],
                            "label": p["provider_label"],
                            "has_epg": bool(p["has_epg"]),
                            "last_import": p["last_successful_import"],
                            "error_count": p["error_count"],
                        }
                        for p in providers
                    ],
                },
                "channels": {
                    "total": channels[0] if channels else 0,
                    "synced": channels[1] if channels else 0,
                    "failed": channels[2] if channels else 0,
                    "in_progress": channels[3] if channels else 0,
                },
                "recent_imports": [
                    {
                        "start": r["start"],
                        "end": r["end"],
                        "fetched": r["programs_fetched"],
                        "inserted": r["programs_inserted"],
                        "updated": r["programs_updated"],
                        "status": r["status"],
                        "created_at": r["created_at"],
                    }
                    for r in recent_imports
                ],
            }
        )

    except Exception as e:
        logger.error(f"Error getting ultimate status: {e}")
        return jsonify({"error": str(e)}), 500


@ultimate_operations_bp.route("/ultimate/providers", methods=["GET"])
def list_ultimate_providers():
    """List discovered providers from Ultimate Backend."""
    try:
        from ...database.connection import get_db
        from .helpers import _ultimate_provider_imports_in_progress, _ultimate_provider_imports_lock

        db = get_db()

        providers = db.fetchall("""
            SELECT 
                up.id,
                up.provider_name,
                up.provider_label,
                up.has_epg,
                up.enabled,
                up.last_discovered_at,
                up.last_successful_import,
                up.error_count,
                COUNT(uc.id) as channel_count
            FROM ultimate_providers up
            LEFT JOIN ultimate_channels uc ON up.id = uc.ultimate_provider_id
            GROUP BY up.id
            ORDER BY up.provider_name
        """)

        with _ultimate_provider_imports_lock:
            in_flight_set = _ultimate_provider_imports_in_progress.copy()

        return jsonify(
            {
                "providers": [
                    {
                        "id": p[0],
                        "name": p[1],
                        "label": p[2],
                        "has_epg": bool(p[3]),
                        "enabled": bool(p[4]),
                        "last_discovered": p[5],
                        "last_import": p[6],
                        "error_count": p[7],
                        "channel_count": p[8],
                        "import_in_progress": p[1] in in_flight_set,
                    }
                    for p in providers
                ]
            }
        )

    except Exception as e:
        logger.error(f"Error listing ultimate providers: {e}")
        return jsonify({"error": str(e)}), 500


@ultimate_operations_bp.route("/ultimate/providers/<int:provider_id>/channels", methods=["GET"])
def list_ultimate_provider_channels(provider_id):
    """List channels for a specific Ultimate Backend provider."""
    try:
        from ...database.connection import get_db

        db = get_db()

        # Get provider info
        provider = db.fetchone(
            "SELECT provider_name, provider_label FROM ultimate_providers WHERE id = ?",
            (provider_id,),
        )

        if not provider:
            return jsonify({"error": "Provider not found"}), 404

        # Get channels
        channels = db.fetchall(
            """
            SELECT 
                uc.id,
                uc.ultimate_channel_id,
                uc.channel_name,
                uc.channel_number,
                uc.logo_url,
                uc.catchup_hours,
                uc.enabled,
                cis.sync_status,
                cis.last_successful_sync,
                cis.program_count
            FROM ultimate_channels uc
            LEFT JOIN channel_import_state cis ON uc.id = cis.ultimate_channel_id
            WHERE uc.ultimate_provider_id = ?
            ORDER BY uc.channel_number, uc.channel_name
        """,
            (provider_id,),
        )

        return jsonify(
            {
                "provider": {
                    "id": provider_id,
                    "name": provider[0],
                    "label": provider[1],
                },
                "channels": [
                    {
                        "id": c[0],
                        "ultimate_id": c[1],
                        "name": c[2],
                        "number": c[3],
                        "logo_url": c[4],
                        "catchup_hours": c[5],
                        "enabled": bool(c[6]),
                        "sync_status": c[7],
                        "last_sync": c[8],
                        "program_count": c[9],
                    }
                    for c in channels
                ],
            }
        )

    except Exception as e:
        logger.error(f"Error listing ultimate provider channels: {e}")
        return jsonify({"error": str(e)}), 500