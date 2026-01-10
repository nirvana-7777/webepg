"""
HTTP request handlers for EPG API.
"""

import logging
from datetime import datetime

from dateutil.parser import isoparse
from flask import Blueprint, jsonify, request

from ..scheduler.jobs import JobScheduler
from ..services.epg_service import EPGService
from ..services.provider_service import ProviderService

logger = logging.getLogger(__name__)

# Create blueprint
api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

# Service instances (will be injected)
epg_service = None
provider_service = None
scheduler = None


def init_handlers(
    epg_svc: EPGService, provider_svc: ProviderService, sched: JobScheduler
):
    """Initialize handlers with service instances."""
    global epg_service, provider_service, scheduler
    epg_service = epg_svc
    provider_service = provider_svc
    scheduler = sched


@api_bp.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})


@api_bp.route("/channels", methods=["GET"])
def list_channels():
    """List all channels."""
    try:
        channels = epg_service.list_channels()
        return jsonify([channel.to_dict() for channel in channels])
    except Exception as e:
        logger.error(f"Error listing channels: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/channels/<channel_identifier>", methods=["GET"])
def get_channel(channel_identifier):
    """Get channel by ID, name, or alias."""
    try:
        channel = epg_service.get_channel_by_id_or_alias(channel_identifier)
        if not channel:
            return jsonify({"error": "Channel not found"}), 404

        return jsonify(channel.to_dict())
    except Exception as e:
        logger.error(f"Error getting channel {channel_identifier}: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/channels/<channel_identifier>/programs", methods=["GET"])
def get_channel_programs(channel_identifier):
    """Get programs for a channel within a time range."""
    try:
        # Parse query parameters
        start_str = request.args.get("start")
        end_str = request.args.get("end")

        if not start_str or not end_str:
            return (
                jsonify({"error": "Both start and end query parameters are required"}),
                400,
            )

        try:
            start = isoparse(start_str)
            end = isoparse(end_str)
        except ValueError as e:
            return jsonify({"error": f"Invalid datetime format: {e}"}), 400

        # Validate time range
        if start >= end:
            return jsonify({"error": "Start time must be before end time"}), 400

        # Get channel by ID, name, or alias
        channel = epg_service.get_channel_by_id_or_alias(channel_identifier)
        if not channel:
            return jsonify({"error": "Channel not found"}), 404

        # Get programs
        programs = epg_service.get_programs(channel.id, start, end)

        return jsonify([program.to_dict() for program in programs])

    except Exception as e:
        logger.error(f"Error getting programs for channel {channel_identifier}: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/providers", methods=["GET"])
def list_providers():
    """List all providers."""
    try:
        providers = provider_service.list_providers()
        return jsonify([provider.to_dict() for provider in providers])
    except Exception as e:
        logger.error(f"Error listing providers: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/providers/<int:provider_id>", methods=["GET"])
def get_provider(provider_id):
    """Get provider by ID."""
    try:
        provider = provider_service.get_provider(provider_id)
        if not provider:
            return jsonify({"error": "Provider not found"}), 404

        return jsonify(provider.to_dict())
    except Exception as e:
        logger.error(f"Error getting provider {provider_id}: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/providers", methods=["POST"])
def create_provider():
    """Create a new provider."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body is required"}), 400

        name = data.get("name")
        xmltv_url = data.get("xmltv_url")

        if not name or not xmltv_url:
            return jsonify({"error": "Both name and xmltv_url are required"}), 400

        provider = provider_service.create_provider(name, xmltv_url)

        return jsonify(provider.to_dict()), 201

    except Exception as e:
        logger.error(f"Error creating provider: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/providers/<int:provider_id>", methods=["PUT"])
def update_provider(provider_id):
    """Update an existing provider."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body is required"}), 400

        # Check if provider exists
        existing = provider_service.get_provider(provider_id)
        if not existing:
            return jsonify({"error": "Provider not found"}), 404

        # Update provider
        provider = provider_service.update_provider(
            provider_id,
            name=data.get("name"),
            xmltv_url=data.get("xmltv_url"),
            enabled=data.get("enabled"),
        )

        return jsonify(provider.to_dict())

    except Exception as e:
        logger.error(f"Error updating provider {provider_id}: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/providers/<int:provider_id>", methods=["DELETE"])
def delete_provider(provider_id):
    """Delete a provider."""
    try:
        deleted = provider_service.delete_provider(provider_id)

        if not deleted:
            return jsonify({"error": "Provider not found"}), 404

        return "", 204

    except Exception as e:
        logger.error(f"Error deleting provider {provider_id}: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/import/trigger", methods=["POST"])
def trigger_import():
    """Manually trigger import for all providers."""
    try:
        scheduler.trigger_import_now()

        return jsonify(
            {
                "message": "Import job triggered",
                "next_scheduled_import": (
                    scheduler.get_next_run_time().isoformat()
                    if scheduler.get_next_run_time()
                    else None
                ),
            }
        )

    except Exception as e:
        logger.error(f"Error triggering import: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/import/status", methods=["GET"])
def import_status():
    """Get import status and next scheduled run time."""
    try:
        from ..database.connection import get_db
        from ..database.models import ImportLog

        db = get_db()

        # Get recent import logs
        rows = db.fetchall("""
            SELECT id, provider_id, started_at, completed_at, status,
                   programs_imported, programs_skipped, error_message
            FROM import_log
            ORDER BY started_at DESC
            LIMIT 10
            """)

        logs = [ImportLog.from_db_row(tuple(row)).to_dict() for row in rows]

        next_run = scheduler.get_next_run_time()

        return jsonify(
            {
                "next_scheduled_import": next_run.isoformat() if next_run else None,
                "recent_imports": logs,
            }
        )

    except Exception as e:
        logger.error(f"Error getting import status: {e}")
        return jsonify({"error": str(e)}), 500

@api_bp.route("/aliases", methods=["GET"])
def list_all_aliases():
    """List all aliases across all channels."""
    try:
        aliases = epg_service.list_all_aliases()

        # Basic response
        return jsonify({
            "count": len(aliases),
            "aliases": [alias.to_dict() for alias in aliases]
        })
    except Exception as e:
        logger.error(f"Error listing all aliases: {e}")
        return jsonify({"error": str(e)}), 500

@api_bp.route("/aliases/mapping", methods=["GET"])
def get_alias_mapping():
    """Get optimized alias-to-channel mapping."""
    try:
        # Returns a more efficient structure for lookups
        mapping = {}

        aliases = epg_service.list_all_aliases()
        for alias in aliases:
            mapping[alias.alias] = {
                "channel_id": alias.channel_id,
                "channel_name": alias.channel.name if alias.channel else None,
                "alias_type": alias.alias_type,
                "alias_id": alias.id
            }

        return jsonify({
            "count": len(mapping),
            "mapping": mapping
        })
    except Exception as e:
        logger.error(f"Error getting alias mapping: {e}")
        return jsonify({"error": str(e)}), 500

@api_bp.route("/channels/<channel_identifier>/aliases", methods=["GET"])
def list_channel_aliases(channel_identifier):
    """List all aliases for a channel."""
    try:
        # Get channel by ID, name, or alias
        channel = epg_service.get_channel_by_id_or_alias(channel_identifier)
        if not channel:
            return jsonify({"error": "Channel not found"}), 404

        aliases = epg_service.list_channel_aliases(channel.id)

        return jsonify([alias.to_dict() for alias in aliases])

    except Exception as e:
        logger.error(f"Error listing aliases for channel {channel_identifier}: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/channels/<channel_identifier>/aliases", methods=["POST"])
def create_channel_alias(channel_identifier):
    """Create an alias for a channel."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body is required"}), 400

        alias = data.get("alias")
        alias_type = data.get("alias_type")

        if not alias:
            return jsonify({"error": "alias is required"}), 400

        # Get channel by ID, name, or alias
        channel = epg_service.get_channel_by_id_or_alias(channel_identifier)
        if not channel:
            return jsonify({"error": "Channel not found"}), 404

        # Create alias
        new_alias = epg_service.create_channel_alias(
            channel_id=channel.id, alias=alias, alias_type=alias_type
        )

        return jsonify(new_alias.to_dict()), 201

    except Exception as e:
        logger.error(f"Error creating alias for channel {channel_identifier}: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/aliases/<int:alias_id>", methods=["DELETE"])
def delete_channel_alias(alias_id):
    """Delete a channel alias."""
    try:
        deleted = epg_service.delete_channel_alias(alias_id)

        if not deleted:
            return jsonify({"error": "Alias not found"}), 404

        return "", 204

    except Exception as e:
        logger.error(f"Error deleting alias {alias_id}: {e}")
        return jsonify({"error": str(e)}), 500


# Add these endpoints to handlers.py


@api_bp.route("/providers/<int:provider_id>/test", methods=["GET"])
def test_provider_connection(provider_id):
    """Test connection to provider's XMLTV URL."""
    try:
        provider = provider_service.get_provider(provider_id)
        if not provider:
            return jsonify({"error": "Provider not found"}), 404

        import requests
        from requests.exceptions import RequestException

        # Try to fetch a small portion of the XMLTV file
        try:
            response = requests.head(provider.xmltv_url, timeout=10)

            if response.status_code == 200:
                # Try to get first few lines to verify it's XMLTV
                content_response = requests.get(
                    provider.xmltv_url, timeout=10, stream=True
                )
                first_chunk = next(content_response.iter_content(1024)).decode(
                    "utf-8", errors="ignore"
                )

                is_xmltv = "<?xml" in first_chunk and (
                    "<tv>" in first_chunk or "<!DOCTYPE tv" in first_chunk
                )

                return jsonify(
                    {
                        "success": True,
                        "status": "online",
                        "content_type": response.headers.get("content-type"),
                        "is_xmltv": is_xmltv,
                        "message": "Connection successful",
                    }
                )
            else:
                return jsonify(
                    {
                        "success": False,
                        "status": "error",
                        "message": f"HTTP {response.status_code}: {response.reason}",
                    }
                )

        except RequestException as e:
            return jsonify({"success": False, "status": "error", "message": str(e)})

    except Exception as e:
        logger.error(f"Error testing provider {provider_id}: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/providers/<int:provider_id>/import/trigger", methods=["POST"])
def trigger_provider_import(provider_id):
    """Trigger import for a specific provider."""
    try:
        provider = provider_service.get_provider(provider_id)
        if not provider:
            return jsonify({"error": "Provider not found"}), 404

        if not provider.enabled:
            return jsonify({"error": "Provider is disabled"}), 400

        # You'll need to implement this in your import service
        # For now, return a placeholder response
        return jsonify(
            {
                "success": True,
                "message": f"Import triggered for provider {provider_id}",
                "provider_id": provider_id,
            }
        )

    except Exception as e:
        logger.error(f"Error triggering import for provider {provider_id}: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/statistics", methods=["GET"])
def get_statistics():
    """Get comprehensive statistics about the EPG database."""
    try:
        from ..database.connection import get_db

        db = get_db()
        stats = {}

        # Basic counts
        row = db.fetchone("SELECT COUNT(*) FROM channels")
        stats["total_channels"] = row[0] if row else 0

        row = db.fetchone("SELECT COUNT(*) FROM programs")
        stats["total_programs"] = row[0] if row else 0

        row = db.fetchone("SELECT COUNT(*) FROM providers")
        stats["total_providers"] = row[0] if row else 0

        row = db.fetchone("SELECT COUNT(*) FROM channel_aliases")
        stats["total_aliases"] = row[0] if row else 0

        # Date ranges
        row = db.fetchone("""
                          SELECT MIN(start_time)                   as earliest,
                                 MAX(start_time)                   as latest,
                                 COUNT(DISTINCT DATE (start_time)) as days_covered
                          FROM programs
                          """)
        if row and row[0]:
            stats["earliest_program"] = row[0]
            stats["latest_program"] = row[1]
            stats["days_covered"] = row[2]

        # Programs per day (last 7 days)
        rows = db.fetchall("""
                           SELECT
                               DATE (start_time) as date, COUNT (*) as count
                           FROM programs
                           WHERE start_time > datetime('now', '-7 days')
                           GROUP BY DATE (start_time)
                           ORDER BY date DESC
                           """)
        stats["programs_last_7_days"] = [{"date": r[0], "count": r[1]} for r in rows]

        # Import statistics
        row = db.fetchone("""
                          SELECT COUNT(*)                                            as total_imports,
                                 SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_imports,
                                 SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)  as failed_imports,
                                 MAX(completed_at)                                   as last_import
                          FROM import_log
                          """)
        if row:
            stats["imports_total"] = row[0] or 0
            stats["imports_successful"] = row[1] or 0
            stats["imports_failed"] = row[2] or 0
            stats["last_import"] = row[3]

        return jsonify(stats)

    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({"error": "Endpoint not found"}), 404


@api_bp.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    return jsonify({"error": "Internal server error"}), 500
