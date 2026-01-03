"""
Flask HTTP server for EPG API.
"""

import logging

from flask import Flask
from flask_cors import CORS

from ..scheduler.jobs import JobScheduler
from ..services.epg_service import EPGService
from ..services.provider_service import ProviderService
from .handlers import api_bp, init_handlers

logger = logging.getLogger(__name__)


def create_app(config: dict, scheduler: JobScheduler) -> Flask:
    """
    Create and configure Flask application.

    Args:
        config: Configuration dictionary
        scheduler: Job scheduler instance

    Returns:
        Configured Flask app
    """
    app = Flask(__name__)

    # Configure CORS if needed
    if config.get("cors_enabled", False):
        CORS(app)

    # Initialize services
    epg_service = EPGService()
    provider_service = ProviderService()

    # Initialize handlers with services
    init_handlers(epg_service, provider_service, scheduler)

    # Register blueprint
    app.register_blueprint(api_bp)

    # Add request logging
    @app.before_request
    def log_request():
        from flask import request

        logger.debug(f"{request.method} {request.path}")

    @app.after_request
    def log_response(response):
        from flask import request

        logger.debug(f"{request.method} {request.path} - {response.status_code}")
        return response

    logger.info("Flask application created")

    return app


def run_server(config: dict, scheduler: JobScheduler):
    """
    Run Flask development server.

    Args:
        config: Configuration dictionary
        scheduler: Job scheduler instance
    """
    app = create_app(config, scheduler)

    host = config.get("host", "0.0.0.0")
    port = config.get("port", 8080)
    debug = config.get("debug", False)

    logger.info(f"Starting server on {host}:{port}")

    app.run(host=host, port=port, debug=debug, threaded=True)
