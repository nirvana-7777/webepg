"""
Alias management endpoints.
"""

import logging
from flask import Blueprint, jsonify, request

from . import ServiceRegistry

logger = logging.getLogger(__name__)

aliases_bp = Blueprint("aliases", __name__)


@aliases_bp.route("/aliases", methods=["GET"])
def list_all_aliases():
    """List all aliases across all channels."""
    try:
        epg_service = ServiceRegistry.epg_service
        assert epg_service is not None, "EPG service not initialized"
        aliases = epg_service.list_all_aliases()

        # Basic response
        return jsonify(
            {"count": len(aliases), "aliases": [alias.to_dict() for alias in aliases]}
        )
    except Exception as e:
        logger.error(f"Error listing all aliases: {e}")
        return jsonify({"error": str(e)}), 500


@aliases_bp.route("/aliases/mapping", methods=["GET"])
def get_alias_mapping():
    """Get optimized alias-to-channel mapping."""
    try:
        epg_service = ServiceRegistry.epg_service
        assert epg_service is not None, "EPG service not initialized"
        mapping = {}

        # Get channels first for lookup
        channels_by_id = {}
        for channel in epg_service.list_channels():
            channels_by_id[channel.id] = channel

        # Get aliases
        aliases = epg_service.list_all_aliases()

        for alias in aliases:
            channel = channels_by_id.get(alias.channel_id)
            mapping[alias.alias] = {
                "channel_id": alias.channel_id,
                "channel_name": channel.name if channel else None,
                "channel_display_name": channel.display_name if channel else None,
                "alias_type": alias.alias_type,
                "alias_id": alias.id,
            }

        return jsonify({"count": len(mapping), "mapping": mapping})
    except Exception as e:
        logger.error(f"Error getting alias mapping: {e}")
        return jsonify({"error": str(e)}), 500


@aliases_bp.route("/channels/<channel_identifier>/aliases", methods=["GET"])
def list_channel_aliases(channel_identifier):
    """List all aliases for a channel."""
    try:
        epg_service = ServiceRegistry.epg_service
        assert epg_service is not None, "EPG service not initialized"

        # Get channel by ID, name, or alias
        channel = epg_service.get_channel_by_id_or_alias(channel_identifier)
        if not channel:
            return jsonify({"error": "Channel not found"}), 404

        aliases = epg_service.list_channel_aliases(channel.id)

        return jsonify([alias.to_dict() for alias in aliases])

    except Exception as e:
        logger.error(f"Error listing aliases for channel {channel_identifier}: {e}")
        return jsonify({"error": str(e)}), 500


@aliases_bp.route("/channels/<channel_identifier>/aliases", methods=["POST"])
def create_channel_alias(channel_identifier):
    """Create an alias for a channel."""
    try:
        epg_service = ServiceRegistry.epg_service
        assert epg_service is not None, "EPG service not initialized"
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


@aliases_bp.route("/aliases/<int:alias_id>", methods=["DELETE"])
def delete_channel_alias(alias_id):
    """Delete a channel alias."""
    try:
        epg_service = ServiceRegistry.epg_service
        assert epg_service is not None, "EPG service not initialized"
        deleted = epg_service.delete_channel_alias(alias_id)

        if not deleted:
            return jsonify({"error": "Alias not found"}), 404

        return "", 204

    except Exception as e:
        logger.error(f"Error deleting alias {alias_id}: {e}")
        return jsonify({"error": str(e)}), 500