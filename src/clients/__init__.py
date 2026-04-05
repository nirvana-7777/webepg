"""HTTP clients for external EPG sources."""

from .base import EPGClient
from .ultimate_backend_client import UltimateBackendClient
from .models import UltimateBackendProgram, UltimateBackendChannel, UltimateBackendProvider

__all__ = [
    "EPGClient",
    "UltimateBackendClient",
    "UltimateBackendProgram",
    "UltimateBackendChannel",
    "UltimateBackendProvider",
]