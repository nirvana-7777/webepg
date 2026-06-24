"""
Channel and program endpoints.
"""

import logging
from flask import Blueprint, jsonify, request
from dateutil.parser import isoparse

from . import ServiceRegistry
from ..utils.time_utils import now_utc, to_utc_isoformat

logger = logging.getLogger(__name__)

channels_bp = Blueprint("channels", __name__)


@channels_bp.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "timestamp": to_utc_isoformat(now_utc())})


@channels_bp.route("/channels", methods=["GET"])
def list_channels():
    """List all channels."""
    try:
        epg_service = ServiceRegistry.epg_service
        assert epg_service is not None, "EPG service not initialized"
        channels = epg_service.list_channels()
        return jsonify([channel.to_dict() for channel in channels])
    except Exception as e:
        logger.error(f"Error listing channels: {e}")
        return jsonify({"error": str(e)}), 500


@channels_bp.route("/channels/<channel_identifier>", methods=["GET"])
def get_channel(channel_identifier):
    """Get channel by ID, name, or alias."""
    try:
        epg_service = ServiceRegistry.epg_service
        assert epg_service is not None, "EPG service not initialized"
        channel = epg_service.get_channel_by_id_or_alias(channel_identifier)
        if not channel:
            return jsonify({"error": "Channel not found"}), 404

        return jsonify(channel.to_dict())
    except Exception as e:
        logger.error(f"Error getting channel {channel_identifier}: {e}")
        return jsonify({"error": str(e)}), 500


@channels_bp.route("/channels/<channel_identifier>/programs", methods=["GET"])
def get_channel_programs(channel_identifier):
    """Get programs for a channel within a time range."""
    try:
        epg_service = ServiceRegistry.epg_service
        assert epg_service is not None, "EPG service not initialized"

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