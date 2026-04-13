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
            Dict with discovery statistics.

        NOTE: This coroutine closes the underlying HTTP client session before
        returning so that callers using asyncio.run() start clean on the next
        invocation (no stale connections bound to a dead event loop).
        """
        logger.info("Starting Ultimate Backend discovery")

        stats = {
            "providers_found": 0,
            "providers_with_epg": 0,
            "channels_found": 0,
            "channels_mapped": 0,
            "errors": [],
        }

        try:
            # Get instance ID (default to 1 for now)
            instance_id = await self._get_or_create_instance()

            # Fetch all providers
            providers = await self.client.get_providers()
            stats["providers_found"] = len(providers)

            for provider_data in providers:
                provider_name = provider_data.get("name")
                if not provider_name:
                    continue

                # Skip providers that are not ready or not enabled
                if not provider_data.get("enabled", True):
                    logger.debug(f"Provider {provider_name} is disabled, skipping")
                    continue
                if not provider_data.get("instance_ready", True):
                    logger.debug(
                        f"Provider {provider_name} instance not ready, skipping"
                    )
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
                            error_msg = (
                                f"Failed to process channel "
                                f"{channel_data.get('Name', 'unknown')}: {e}"
                            )
                            logger.error(error_msg)
                            stats["errors"].append(error_msg)

                except Exception as e:
                    error_msg = f"Failed to get channels for {provider_name}: {e}"
                    logger.error(error_msg)
                    stats["errors"].append(error_msg)

            # Update discovery timestamp
            await self._update_discovery_timestamp(instance_id)

        finally:
            # Always close the session so the next asyncio.run() call starts
            # with a fresh session on a fresh event loop.
            await self.client.close()

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
        This method is idempotent - it will create missing mappings on subsequent runs.
        """
        db = get_db()

        ultimate_channel_id = str(channel_data.get("Id", channel_data.get("id", "")))
        channel_name = channel_data.get("Name", channel_data.get("name", ""))

        if not ultimate_channel_id or not channel_name:
            logger.warning(f"Invalid channel data (missing Id or Name): {channel_data}")
            return None

        # Safely coerce values
        raw_number = channel_data.get("ChannelNumber")
        try:
            channel_number = int(raw_number) if raw_number is not None else 0
        except (ValueError, TypeError):
            channel_number = 0

        raw_catchup = channel_data.get("CatchupHours")
        try:
            catchup_hours = int(raw_catchup) if raw_catchup is not None else 168
        except (ValueError, TypeError):
            catchup_hours = 168

        # Get or create logical channel in EPG Service
        logical_name = f"{provider_name}:{ultimate_channel_id}"
        channel = self.epg_service.get_or_create_channel(
            name=logical_name,
            display_name=channel_name,
            icon_url=channel_data.get("LogoUrl", channel_data.get("logo_url")),
        )

        logger.info(f"Got/Created logical channel: ID={channel.id}, Name={logical_name}")

        # Check if channel already exists in ultimate_channels
        row = db.fetchone(
            """
            SELECT id FROM ultimate_channels
            WHERE ultimate_provider_id = ? AND ultimate_channel_id = ?
            """,
            (provider_id, ultimate_channel_id),
        )

        if row:
            ultimate_channel_db_id = row[0]
            # Update existing ultimate_channel with latest data
            db.execute(
                """
                UPDATE ultimate_channels
                SET channel_name = ?, channel_number = ?, logo_url = ?,
                    catchup_hours = ?, live_id = ?, stream_uid = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    channel_name,
                    channel_number,
                    channel_data.get("LogoUrl", channel_data.get("logo_url")),
                    catchup_hours,
                    channel_data.get("LiveId", channel_data.get("live_id")),
                    channel_data.get("StreamUid", channel_data.get("stream_uid")),
                    datetime.utcnow().isoformat(),
                    ultimate_channel_db_id,
                ),
            )
            logger.debug(f"Updated ultimate_channel entry: ID={ultimate_channel_db_id}")
        else:
            # Create new ultimate_channel
            cursor = db.execute(
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
                    channel_number,
                    channel_data.get("LogoUrl", channel_data.get("logo_url")),
                    catchup_hours,
                    channel_data.get("LiveId", channel_data.get("live_id")),
                    channel_data.get("StreamUid", channel_data.get("stream_uid")),
                ),
            )
            ultimate_channel_db_id = cursor.lastrowid
            logger.info(f"Created ultimate_channel entry: ID={ultimate_channel_db_id}")

        # ===================================================================
        # FIX: ALWAYS ensure mapping exists - this runs for both new AND existing channels
        # ===================================================================
        existing_mapping = db.fetchone(
            """
            SELECT id FROM ultimate_channel_mappings 
            WHERE ultimate_channel_id = ? AND channel_id = ?
            """,
            (ultimate_channel_db_id, channel.id),
        )

        if not existing_mapping:
            db.execute(
                """
                INSERT INTO ultimate_channel_mappings (ultimate_channel_id, channel_id)
                VALUES (?, ?)
                """,
                (ultimate_channel_db_id, channel.id),
            )
            logger.info(f"Created mapping: ultimate_channel_id={ultimate_channel_db_id} -> channel_id={channel.id}")
        else:
            logger.debug(f"Mapping already exists: ID={existing_mapping[0]}")

        # Always ensure import state exists
        existing_state = db.fetchone(
            """
            SELECT id FROM channel_import_state WHERE ultimate_channel_id = ?
            """,
            (ultimate_channel_db_id,),
        )

        if not existing_state:
            db.execute(
                """
                INSERT INTO channel_import_state (ultimate_channel_id, sync_status)
                VALUES (?, 'pending')
                """,
                (ultimate_channel_db_id,),
            )
            logger.info(f"Created import state for channel {ultimate_channel_db_id}")
        else:
            # Also reset failed channels on rediscovery
            state_row = db.fetchone(
                "SELECT sync_status FROM channel_import_state WHERE ultimate_channel_id = ?",
                (ultimate_channel_db_id,),
            )
            if state_row and state_row[0] == 'failed':
                db.execute(
                    """
                    UPDATE channel_import_state
                    SET sync_status = 'pending', last_error = NULL, updated_at = ?
                    WHERE ultimate_channel_id = ?
                    """,
                    (datetime.utcnow().isoformat(), ultimate_channel_db_id),
                )
                logger.info(f"Reset failed import state for channel {ultimate_channel_db_id}")

        logger.info(
            f"Mapped channel: {channel_name} (ID: {ultimate_channel_id}) "
            f"-> logical channel {channel.id} (ultimate_channel_db_id={ultimate_channel_db_id})"
        )

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