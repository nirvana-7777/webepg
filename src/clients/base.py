"""
Abstract base class for EPG clients.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List


class EPGClient(ABC):
    """Abstract base class for EPG data sources."""

    @abstractmethod
    async def get_providers(self) -> List[Dict]:
        """Get list of available providers."""
        pass

    @abstractmethod
    async def get_channels(self, provider_name: str) -> List[Dict]:
        """Get channels for a provider."""
        pass

    @abstractmethod
    async def get_epg(
            self,
            provider_name: str,
            channel_id: str,
            start_time: datetime,
            end_time: datetime
    ) -> List[Dict]:
        """Get EPG programs for a channel within time range."""
        pass

    @abstractmethod
    async def has_epg(self, provider_name: str) -> bool:
        """Check if provider has EPG capability."""
        pass