"""
Ultimate Backend channel endpoints.
"""

import logging
import threading
import asyncio
from flask import Blueprint, jsonify

from .helpers import resolve_ultimate_provider, resolve_ultimate_channel
from .. import ServiceRegistry

logger = logging.getLogger(__name__)

ultimate_channels_bp = Blueprint("ultimate_channels", __name__)


@ultimate_channels_bp.route(
    "/ultimate/providers/<identifier>/channels/<channel_identifier>/import",
    methods=["POST"],
)
def trigger_ultimate_provider_channel_import(identifier, channel_identifier):
    """
    Trigger an incremental import for one channel, scoped to one provider.

    `identifier` may be the provider's numeric id or provider_name.
    `channel_identifier` may be the numeric ultimate_channels.id or the
    provider's own ultimate_channel_id string.

    Note: /ultimate/channels/<int:channel_id>/import (global DB id, no
    provider scoping) is unchanged and still works for existing callers.
    """
    scheduler = ServiceRegistry.scheduler
    ultimate_import_service = getattr(scheduler, "ultimate_import_service", None)
    if not scheduler or not ultimate_import_service:
        return jsonify({"error": "Ultimate Backend not initialized"}), 500

    try:
        provider = resolve_ultimate_provider(identifier)
        if not provider:
            return jsonify({"error": f"Ultimate Backend provider not found: {identifier}"}), 404

        channel = resolve_ultimate_channel(provider["id"], channel_identifier)
        if not channel:
            return jsonify(
                {
                    "error": (
                        f"Channel not found: {channel_identifier} "
                        f"for provider '{provider['provider_name']}'"
                    )
                }
            ), 404

        db_id, ultimate_channel_id, channel_name, logical_channel_id = channel
        provider_name = provider["provider_name"]

        def run_import():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
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
                logger.error(f"Failed to import channel '{channel_name}': {exc}")
            finally:
                loop.close()

        threading.Thread(target=run_import, daemon=True).start()

        return jsonify(
            {
                "message": f"Import triggered for channel '{channel_name}'",
                "provider_name": provider_name,
                "channel_id": db_id,
                "ultimate_channel_id": ultimate_channel_id,
                "channel_name": channel_name,
            }
        ), 202

    except Exception as e:
        logger.error(
            f"Error triggering channel import for provider {identifier}, "
            f"channel {channel_identifier}: {e}"
        )
        return jsonify({"error": str(e)}), 500


@ultimate_channels_bp.route("/ultimate/channels/<int:channel_id>/import", methods=["POST"])
def trigger_ultimate_channel_import(channel_id):
    """Manually trigger import for a specific Ultimate Backend channel."""
    scheduler = ServiceRegistry.scheduler
    ultimate_import_service = getattr(scheduler, "ultimate_import_service", None)

    if not scheduler or not ultimate_import_service:
        return jsonify({"error": "Ultimate Backend not initialized"}), 500

    try:
        from ...database.connection import get_db

        db = get_db()
        channel = db.fetchone(
            """
            SELECT 
                uc.id,
                uc.ultimate_channel_id,
                uc.channel_name,
                up.provider_name,
                ucm.channel_id as logical_channel_id
            FROM ultimate_channels uc
            JOIN ultimate_providers up ON uc.ultimate_provider_id = up.id
            JOIN ultimate_channel_mappings ucm ON uc.id = ucm.ultimate_channel_id
            WHERE uc.id = ?
        """,
            (channel_id,),
        )

        if not channel:
            return jsonify({"error": "Channel not found"}), 404

        def run_import():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                ultimate_import_service.incremental_import_channel(
                    ultimate_channel_db_id=channel[0],
                    provider_name=channel[3],
                    ultimate_channel_id=channel[1],
                    logical_channel_id=channel[4],
                    channel_name=channel[2],
                )
            )
            loop.close()

        threading.Thread(target=run_import, daemon=True).start()

        return jsonify(
            {
                "message": f"Import triggered for channel {channel[2]}",
                "channel_id": channel_id,
                "channel_name": channel[2],
            }
        )

    except Exception as e:
        logger.error(f"Error triggering channel import: {e}")
        return jsonify({"error": str(e)}), 500