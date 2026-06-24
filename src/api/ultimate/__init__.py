"""
Ultimate Backend API endpoints.

This package contains all Ultimate Backend-related endpoints organized
by domain: providers, channels, and operations (grid import, discovery).
"""

from flask import Blueprint

# Parent Ultimate Blueprint
ultimate_bp = Blueprint("ultimate", __name__)


def init_ultimate():
    """Initialize ultimate blueprint with sub-blueprints."""
    from .providers import ultimate_providers_bp
    from .channels import ultimate_channels_bp
    from .operations import ultimate_operations_bp

    ultimate_bp.register_blueprint(ultimate_providers_bp)
    ultimate_bp.register_blueprint(ultimate_channels_bp)
    ultimate_bp.register_blueprint(ultimate_operations_bp)
    return ultimate_bp


# Auto-initialize on import
init_ultimate()