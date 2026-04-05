"""
Discovery service for Ultimate Backend providers and channels.
"""

import logging
from datetime import datetime
from typing import Dict, Optional

from ..clients.ultimate_backend_client import UltimateBackendClient
from ..database.connection import get_db
from .epg_service import EPGService
from .provider_service import ProviderService

logger = logging.getLogger(__name__)


class UltimateBackendDiscoveryService:
    """Discovers providers and channels from Ultimate Backend."""

    def __init__(self, client: UltimateBackendClient):
        self.client = client
        self.epg_service = EPGService()
        self.provider_service = ProviderService()

    async def discover_all(self) -> Dict:
        """
        Discover all providers and their channels.

        Returns:
            Dict with discovery statistics
        """
        logger.info("Starting Ultimate Backend discovery")

        stats = {
            "providers_found": 0,
            "providers_with_epg": 0,
            "channels_found": 0,
            "channels_mapped": 0,
            "errors": [],
        }

        # Get instance ID (default to 1 for now)
        instance_id = await self._get_or_create_instance()

        # Fetch all providers
        providers = await self.client.get_providers()
        stats["providers_found"] = len(providers)

        for provider_data in providers:
            provider_name = provider_data.get("name")
            if not provider_name:
                continue

            logger.info(f"Discovering provider: {provider_name}")

            # Check if provider has EPG
            has_epg = await self.client.has_epg(provider_name)

            if not has_epg:
                logger.debug(f"Provider {provider_name} has no EPG, skipping")
                continue

            stats["providers_with_epg"] += 1

            # Create or update provider record
            provider_id = await self._upsert_provider(
                instance_id=instance_id,
                provider_name=provider_name,
                provider_label=provider_data.get("label", provider_name),
                has_epg=has_epg,
            )

            # Discover channels for this provider
            try:
                channels = await self.client.get_channels(provider_name)
                stats["channels_found"] += len(channels)

                for channel_data in channels:
                    try:
                        channel_id = await self._process_channel(
                            provider_id=provider_id,
                            provider_name=provider_name,
                            channel_data=channel_data,
                        )
                        if channel_id:
                            stats["channels_mapped"] += 1
                    except Exception as e:
                        error_msg = f"Failed to process channel {channel_data.get('Name', 'unknown')}: {e}"
                        logger.error(error_msg)
                        stats["errors"].append(error_msg)

            except Exception as e:
                error_msg = f"Failed to get channels for {provider_name}: {e}"
                logger.error(error_msg)
                stats["errors"].append(error_msg)

        # Update discovery timestamp
        await self._update_discovery_timestamp(instance_id)

        logger.info(f"Discovery complete: {stats}")
        return stats

    async def _get_or_create_instance(self) -> int:
        """Get or create the default Ultimate Backend instance."""
        db = get_db()

        row = db.fetchone(
            "SELECT id FROM ultimate_backend_instances WHERE name = 'main'"
        )

        if row:
            return row[0]

        db.execute(
            "INSERT INTO ultimate_backend_instances (name, base_url) VALUES (?, ?)",
            ("main", self.client.base_url),
        )

        row = db.fetchone("SELECT last_insert_rowid()")
        return row[0]

    @staticmethod
    async def _upsert_provider(
            instance_id: int,
            provider_name: str,
            provider_label: str,
            has_epg: bool,
    ) -> int:
        """Insert or update provider record."""
        db = get_db()

        now = datetime.utcnow().isoformat()

        # Check if exists
        row = db.fetchone(
            "SELECT id FROM ultimate_providers WHERE instance_id = ? AND provider_name = ?",
            (instance_id, provider_name),
        )

        if row:
            # Update existing
            db.execute(
                """
                UPDATE ultimate_providers
                SET provider_label = ?, has_epg = ?, updated_at = ?, last_discovered_at = ?
                WHERE id = ?
                """,
                (provider_label, 1 if has_epg else 0, now, now, row[0]),
            )
            return row[0]
        else:
            # Insert new
            db.execute(
                """
                INSERT INTO ultimate_providers (
                    instance_id, provider_name, provider_label, has_epg, last_discovered_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (instance_id, provider_name, provider_label, 1 if has_epg else 0, now),
            )
            row = db.fetchone("SELECT last_insert_rowid()")
            return row[0]

    async def _process_channel(
            self,
            provider_id: int,
            provider_name: str,
            channel_data: Dict,
    ) -> Optional[int]:
        """
        Process a single channel: create logical channel and mapping.

        Returns:
            EPG Service channel ID if created, None otherwise
        """
        db = get_db()

        ultimate_channel_id = str(channel_data.get("Id", ""))
        channel_name = channel_data.get("Name", "")

        if not ultimate_channel_id or not channel_name:
            logger.warning(f"Invalid channel data: {channel_data}")
            return None

        # Check if channel already exists in ultimate_channels
        row = db.fetchone(
            "SELECT id FROM ultimate_channels WHERE ultimate_provider_id = ? AND ultimate_channel_id = ?",
            (provider_id, ultimate_channel_id),
        )

        if row:
            ultimate_channel_db_id = row[0]
            # Check if mapping exists
            mapping_row = db.fetchone(
                "SELECT channel_id FROM ultimate_channel_mappings WHERE ultimate_channel_id = ?",
                (ultimate_channel_db_id,),
            )
            if mapping_row:
                return mapping_row[0]

        # Create or get logical channel in EPG Service
        # Use provider_name:ultimate_channel_id as unique name
        logical_name = f"{provider_name}:{ultimate_channel_id}"

        channel = self.epg_service.get_or_create_channel(
            name=logical_name,
            display_name=channel_name,
            icon_url=channel_data.get("LogoUrl"),
        )

        if not channel:
            logger.error(f"Failed to create logical channel for {logical_name}")
            return None

        # Insert or update ultimate_channels
        if row:
            ultimate_channel_db_id = row[0]
            # Update existing
            db.execute(
                """
                UPDATE ultimate_channels
                SET channel_name = ?, channel_number = ?, logo_url = ?,
                    catchup_hours = ?, live_id = ?, stream_uid = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    channel_name,
                    channel_data.get("ChannelNumber", 0),
                    channel_data.get("LogoUrl"),
                    channel_data.get("CatchupHours", 168),
                    channel_data.get("LiveId"),
                    channel_data.get("StreamUid"),
                    datetime.utcnow().isoformat(),
                    ultimate_channel_db_id,
                ),
            )
        else:
            # Insert new
            db.execute(
                """
                INSERT INTO ultimate_channels (
                    ultimate_provider_id, ultimate_channel_id, channel_name,
                    channel_number, logo_url, catchup_hours, live_id, stream_uid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider_id,
                    ultimate_channel_id,
                    channel_name,
                    channel_data.get("ChannelNumber", 0),
                    channel_data.get("LogoUrl"),
                    channel_data.get("CatchupHours", 168),
                    channel_data.get("LiveId"),
                    channel_data.get("StreamUid"),
                ),
            )
            row = db.fetchone("SELECT last_insert_rowid()")
            ultimate_channel_db_id = row[0]

        # Create mapping
        db.execute(
            """
            INSERT OR IGNORE INTO ultimate_channel_mappings (ultimate_channel_id, channel_id)
            VALUES (?, ?)
            """,
            (ultimate_channel_db_id, channel.id),
        )

        # Initialize import state
        db.execute(
            """
            INSERT OR IGNORE INTO channel_import_state (ultimate_channel_id, sync_status)
            VALUES (?, 'pending')
            """,
            (ultimate_channel_db_id,),
        )

        logger.info(f"Mapped channel: {channel_name} (ID: {ultimate_channel_id}) -> logical channel {channel.id}")

        return channel.id

    @staticmethod
    async def _update_discovery_timestamp(instance_id: int):
        """Update last_discovered_at for all providers in this instance."""
        db = get_db()
        now = datetime.utcnow().isoformat()

        db.execute(
            "UPDATE ultimate_providers SET last_discovered_at = ? WHERE instance_id = ?",
            (now, instance_id),
        )