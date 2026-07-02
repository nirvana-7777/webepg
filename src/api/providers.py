"""
Standard XMLTV provider endpoints.
"""

import logging
import gzip
import os
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request, Response, send_file
from dateutil.parser import isoparse

from . import ServiceRegistry

logger = logging.getLogger(__name__)

providers_bp = Blueprint("providers", __name__)


@providers_bp.route("/providers", methods=["GET"])
def list_providers():
    """List all providers."""
    try:
        provider_service = ServiceRegistry.provider_service
        assert provider_service is not None, "Provider service not initialized"
        providers = provider_service.list_providers()
        return jsonify([provider.to_dict() for provider in providers])
    except Exception as e:
        logger.error(f"Error listing providers: {e}")
        return jsonify({"error": str(e)}), 500


@providers_bp.route("/providers/<int:provider_id>", methods=["GET"])
def get_provider(provider_id):
    """Get provider by ID."""
    try:
        provider_service = ServiceRegistry.provider_service
        assert provider_service is not None, "Provider service not initialized"
        provider = provider_service.get_provider(provider_id)
        if not provider:
            return jsonify({"error": "Provider not found"}), 404

        return jsonify(provider.to_dict())
    except Exception as e:
        logger.error(f"Error getting provider {provider_id}: {e}")
        return jsonify({"error": str(e)}), 500


@providers_bp.route("/providers", methods=["POST"])
def create_provider():
    """Create a new provider."""
    try:
        provider_service = ServiceRegistry.provider_service
        assert provider_service is not None, "Provider service not initialized"
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


@providers_bp.route("/providers/<int:provider_id>", methods=["PUT"])
def update_provider(provider_id):
    """Update an existing provider."""
    try:
        provider_service = ServiceRegistry.provider_service
        assert provider_service is not None, "Provider service not initialized"
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


@providers_bp.route("/providers/<int:provider_id>", methods=["DELETE"])
def delete_provider(provider_id):
    """Delete a provider."""
    try:
        provider_service = ServiceRegistry.provider_service
        assert provider_service is not None, "Provider service not initialized"
        deleted = provider_service.delete_provider(provider_id)

        if not deleted:
            return jsonify({"error": "Provider not found"}), 404

        return "", 204

    except Exception as e:
        logger.error(f"Error deleting provider {provider_id}: {e}")
        return jsonify({"error": str(e)}), 500


@providers_bp.route("/providers/<int:provider_id>/test", methods=["GET"])
def test_provider_connection(provider_id):
    """Test connection to provider's XMLTV URL."""
    try:
        provider_service = ServiceRegistry.provider_service
        assert provider_service is not None, "Provider service not initialized"
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


@providers_bp.route("/providers/<identifier>/epg.xml", methods=["GET"])
def export_provider_epg_xml(identifier):
    """
    Export EPG data for a provider as XMLTV.

    Args:
        identifier: Provider ID or name

    Query params:
        start: Optional start time (ISO format, default: 7 days ago)
        end: Optional end time (ISO format, default: 7 days from now)
    """
    try:
        epg_service = ServiceRegistry.epg_service
        assert epg_service is not None, "EPG service not initialized"
        from ..parsers.xmltv_serializer import XMLTVSerializer

        # Parse time range parameters
        start_str = request.args.get("start")
        end_str = request.args.get("end")

        if start_str:
            try:
                start_time = isoparse(start_str)
            except ValueError as e:
                return jsonify({"error": f"Invalid start datetime: {e}"}), 400
        else:
            # Default: 7 days ago at midnight UTC
            start_time = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=7)

        if end_str:
            try:
                end_time = isoparse(end_str)
            except ValueError as e:
                return jsonify({"error": f"Invalid end datetime: {e}"}), 400
        else:
            # Default: 7 days from now at midnight UTC
            end_time = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=7)

        # Get provider by ID or name
        provider = epg_service.get_provider_by_id_or_name(identifier)
        if not provider:
            return jsonify({"error": f"Provider not found: {identifier}"}), 404

        # Get channels and programs
        channels, programs = epg_service.get_provider_programs_for_export(
            provider_id=provider.id,
            start_time=start_time,
            end_time=end_time,
        )

        # Serialize to XMLTV
        serializer = XMLTVSerializer()

        from ..config import load_config

        config = load_config()
        config.get_section(
            "server"
        )  # Load server config (used by serializer internally if needed)

        xml_output = serializer.serialize_tv(
            channels=channels,
            programs=programs,
            generator_info_name="EPG Service/1.0.0",
            generator_info_url="https://github.com/your-repo/epg-service",
            source_info_name=provider.name,
            source_info_url=provider.xmltv_url if provider.xmltv_url else None,
        )

        return Response(
            xml_output,
            mimetype="application/xml",
            headers={
                "Content-Disposition": (
                    f'inline; filename="epg_{provider.name}_'
                    f'{start_time.strftime("%Y%m%d")}_{end_time.strftime("%Ym%d")}.xml"'
                )
            },
        )

    except Exception as e:
        logger.error(f"Error exporting XMLTV for provider {identifier}: {e}")
        return jsonify({"error": str(e)}), 500


@providers_bp.route("/providers/<identifier>/epg.xml.gz", methods=["GET"])
def export_provider_epg_xml_gz(identifier):
    """
    Export EPG data for a provider as compressed XMLTV (.xml.gz).

    Args:
        identifier: Provider ID or name

    Query params:
        start: Optional start time (ISO format, default: 7 days ago)
        end: Optional end time (ISO format, default: 7 days from now)
        stream: If true, stream the response (default: true)
        cache: If true, use cached file if available (default: false)
    """
    try:
        epg_service = ServiceRegistry.epg_service
        assert epg_service is not None, "EPG service not initialized"
        from ..parsers.xmltv_serializer import XMLTVSerializer

        # Parse time range parameters
        start_str = request.args.get("start")
        end_str = request.args.get("end")
        use_cache = request.args.get("cache", default=False, type=bool)

        # Get provider
        provider = epg_service.get_provider_by_id_or_name(identifier)
        if not provider:
            return jsonify({"error": f"Provider not found: {identifier}"}), 404

        # Check for cached file if cache is enabled
        from ..config import load_config
        config = load_config()
        export_dir = config.get("export_dir", "/tmp/epg_exports")
        cache_filename = f"epg_{provider.name.replace(' ', '_')}.xml.gz"
        cache_path = os.path.join(export_dir, cache_filename)

        if use_cache and os.path.exists(cache_path):
            # Check if cache is fresh (less than 24 hours old)
            mtime = os.path.getmtime(cache_path)
            age_hours = (datetime.now().timestamp() - mtime) / 3600
            if age_hours < 24:
                logger.info(f"Serving cached export for {provider.name} ({age_hours:.1f}h old)")
                return send_file(
                    cache_path,
                    mimetype="application/gzip",
                    as_attachment=True,
                    download_name=f"epg_{provider.name.replace(' ', '_')}.xml.gz",
                )
            else:
                logger.info(f"Cache expired for {provider.name} ({age_hours:.1f}h old)")

        # Parse time range parameters with defaults
        if start_str:
            try:
                start_time = isoparse(start_str)
            except ValueError as e:
                return jsonify({"error": f"Invalid start datetime: {e}"}), 400
        else:
            start_time = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=7)

        if end_str:
            try:
                end_time = isoparse(end_str)
            except ValueError as e:
                return jsonify({"error": f"Invalid end datetime: {e}"}), 400
        else:
            end_time = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=7)

        # Get channels and programs
        channels, programs = epg_service.get_provider_programs_for_export(
            provider_id=provider.id,
            start_time=start_time,
            end_time=end_time,
        )

        if not channels:
            return jsonify({"error": "No channels found for provider"}), 404

        # Serialize to XMLTV
        serializer = XMLTVSerializer()
        xml_output = serializer.serialize_tv(
            channels=channels,
            programs=programs,
            generator_info_name="EPG Service/1.0.0",
            generator_info_url="https://github.com/your-repo/epg-service",
            source_info_name=provider.name,
            source_info_url=provider.xmltv_url if provider.xmltv_url else None,
        )

        # Compress and return
        import io

        # Create in-memory gzip file
        gz_buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=gz_buffer, mode='wb', compresslevel=6) as gz_file:
            gz_file.write(xml_output.encode('utf-8'))
        gz_buffer.seek(0)

        # Save to cache if cache is enabled
        if use_cache:
            try:
                os.makedirs(export_dir, exist_ok=True)
                with open(cache_path, 'wb') as f:
                    f.write(gz_buffer.getvalue())
                logger.info(f"Cached export for {provider.name} at {cache_path}")
            except Exception as e:
                logger.warning(f"Failed to cache export for {provider.name}: {e}")

        # Return compressed response
        filename = f"epg_{provider.name.replace(' ', '_')}_{start_time.strftime('%Y%m%d')}_{end_time.strftime('%Y%m%d')}.xml.gz"

        return Response(
            gz_buffer.getvalue(),
            mimetype="application/gzip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "public, max-age=3600",
            },
        )

    except Exception as e:
        logger.error(f"Error exporting compressed XMLTV for provider {identifier}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@providers_bp.route("/providers/<identifier>/epg.xml/channels", methods=["GET"])
def export_provider_channels_xml(identifier):
    """
    Export only channels for a provider as XMLTV.

    Args:
        identifier: Provider ID or name
    """
    try:
        epg_service = ServiceRegistry.epg_service
        assert epg_service is not None, "EPG service not initialized"
        from ..parsers.xmltv_serializer import XMLTVSerializer
        from xml.etree.ElementTree import Element, tostring
        from xml.dom import minidom

        # Get provider by ID or name
        provider = epg_service.get_provider_by_id_or_name(identifier)
        if not provider:
            return jsonify({"error": f"Provider not found: {identifier}"}), 404

        # Get channels only (time range not needed)
        channels, _ = epg_service.get_provider_programs_for_export(
            provider_id=provider.id,
            start_time=None,
            end_time=None,
        )

        # Serialize only channels
        serializer = XMLTVSerializer()
        tv_elem = Element("tv")
        for channel in channels:
            tv_elem.append(serializer.serialize_channel(channel))

        rough_string = tostring(tv_elem, "utf-8")
        reparsed = minidom.parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")

        # Add DOCTYPE
        xml_parts = pretty_xml.split("\n", 1)
        if len(xml_parts) > 1:
            result = f"{xml_parts[0]}\n{serializer.DOCTYPE}\n{xml_parts[1]}"
        else:
            result = pretty_xml

        return Response(
            result,
            mimetype="application/xml",
            headers={
                "Content-Disposition": f'inline; filename="channels_{provider.name}.xml"'
            },
        )

    except Exception as e:
        logger.error(f"Error exporting channels for provider {identifier}: {e}")
        return jsonify({"error": str(e)}), 500


@providers_bp.route("/providers/<int:provider_id>/import/trigger", methods=["POST"])
def trigger_provider_import(provider_id):
    """Trigger import for a specific provider."""
    try:
        provider_service = ServiceRegistry.provider_service
        assert provider_service is not None, "Provider service not initialized"
        provider = provider_service.get_provider(provider_id)
        if not provider:
            return jsonify({"error": "Provider not found"}), 404

        if not provider.enabled:
            return jsonify({"error": "Provider is disabled"}), 400

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