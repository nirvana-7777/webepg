"""HTTP clients for external EPG sources."""

from .base import EPGClient
from .models import (
    UltimateBackendChannel,
    UltimateBackendProgram,
    UltimateBackendProvider,
)
from .ultimate_backend_client import UltimateBackendClient

__all__ = [
    "EPGClient",
    "UltimateBackendClient",
    "UltimateBackendProgram",
    "UltimateBackendChannel",
    "UltimateBackendProvider",
]
