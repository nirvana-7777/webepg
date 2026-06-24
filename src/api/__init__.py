"""
API Blueprint registration and service registry.
"""

from flask import Blueprint, jsonify

# Service Registry
class ServiceRegistry:
    epg_service = None
    provider_service = None
    scheduler = None

    @classmethod
    def init(cls, epg_svc, provider_svc, sched):
        """Initialize service instances."""
        cls.epg_service = epg_svc
        cls.provider_service = provider_svc
        cls.scheduler = sched


# Parent Blueprint
api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


def init_handlers(epg_svc, provider_svc, sched):
    """Initialize handlers with service instances and register sub-blueprints."""
    ServiceRegistry.init(epg_svc, provider_svc, sched)

    # Import sub-blueprints here to avoid circular imports
    from .channels import channels_bp
    from .providers import providers_bp
    from .aliases import aliases_bp
    from .admin import admin_bp
    from .ultimate import ultimate_bp

    # Register sub-blueprints
    api_bp.register_blueprint(channels_bp)
    api_bp.register_blueprint(providers_bp)
    api_bp.register_blueprint(aliases_bp)
    api_bp.register_blueprint(admin_bp)
    api_bp.register_blueprint(ultimate_bp)


# Error Handlers
@api_bp.errorhandler(404)
def not_found(_error):
    """Handle 404 errors."""
    return jsonify({"error": "Endpoint not found"}), 404


@api_bp.errorhandler(500)
def internal_error(_error):
    """Handle 500 errors."""
    return jsonify({"error": "Internal server error"}), 500