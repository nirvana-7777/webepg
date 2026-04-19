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

                # Discover channels for this provider (also gives us EPG support info)
                try:
                    channel_list = await self.client.get_channels(provider_name)
                    has_epg = channel_list.epg_window.implements_epg

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
                        epg_window=channel_list.epg_window,
                    )

                    # Process channels
                    stats["channels_found"] += len(channel_list.channels)

                    for channel in channel_list.channels:
                        try:
                            channel_id = await self._process_channel(
                                provider_id=provider_id,
                                provider_name=provider_name,
                                channel=channel,
                            )
                            if channel_id:
                                stats["channels_mapped"] += 1
                        except Exception as e:
                            error_msg = (
                                f"Failed to process channel "
                                f"{channel.name}: {e}"
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
            epg_window=None,
    ) -> int:
        """
        Insert or update provider record in both ultimate_providers and providers tables.

        Args:
            instance_id: Ultimate Backend instance ID
            provider_name: Provider name (unique identifier)
            provider_label: Human-readable provider label
            has_epg: Whether provider has EPG capability
            epg_window: EPGWindow object with window settings

        Returns:
            Provider ID from ultimate_providers table
        """
        db = get_db()
        now = datetime.utcnow().isoformat()

        # ======================================================================
        # Step 1: Upsert to ultimate_providers table
        # ======================================================================

        # Check if exists in ultimate_providers
        row = db.fetchone(
            "SELECT id FROM ultimate_providers WHERE instance_id = ? AND provider_name = ?",
            (instance_id, provider_name),
        )

        if row:
            # Update existing ultimate_providers record
            db.execute(
                """
                UPDATE ultimate_providers
                SET provider_label = ?,
                    has_epg = ?,
                    updated_at = ?,
                    last_discovered_at = ?
                WHERE id = ?
                """,
                (provider_label, 1 if has_epg else 0, now, now, row[0]),
            )
            ultimate_provider_id = row[0]
            logger.debug(f"Updated ultimate_provider: {provider_name} (ID: {ultimate_provider_id})")
        else:
            # Insert new ultimate_providers record
            cursor = db.execute(
                """
                INSERT INTO ultimate_providers (
                    instance_id,
                    provider_name,
                    provider_label,
                    has_epg,
                    last_discovered_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (instance_id, provider_name, provider_label, 1 if has_epg else 0, now, now, now),
            )
            ultimate_provider_id = cursor.lastrowid
            logger.info(f"Created ultimate_provider: {provider_name} (ID: {ultimate_provider_id})")

        # ======================================================================
        # Step 2: Sync to unified providers table
        # ======================================================================

        # Check if provider exists in providers table
        existing_provider = db.fetchone(
            "SELECT id, source_type FROM providers WHERE name = ?",
            (provider_name,),
        )

        if existing_provider:
            # Update existing provider record
            db.execute(
                """
                UPDATE providers
                SET display_name = ?,
                    source_type = 'ultimate_backend',
                    ultimate_instance_id = ?,
                    has_epg = ?,
                    enabled = 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (provider_label, instance_id, 1 if has_epg else 0, now, existing_provider[0]),
            )
            logger.debug(f"Updated providers table for: {provider_name}")
            provider_id = existing_provider[0]
        else:
            # Insert new provider record
            cursor = db.execute(
                """
                INSERT INTO providers (
                    name,
                    display_name,
                    source_type,
                    ultimate_instance_id,
                    has_epg,
                    enabled,
                    created_at,
                    updated_at
                ) VALUES (?, ?, 'ultimate_backend', ?, ?, 1, ?, ?)
                """,
                (provider_name, provider_label, instance_id, 1 if has_epg else 0, now, now),
            )
            provider_id = cursor.lastrowid
            logger.info(f"Created providers table entry for: {provider_name}")

        # ======================================================================
        # Step 3: Also ensure provider_epg_config exists for EPG-enabled providers
        # ======================================================================

        if has_epg:
            # Check if EPG config exists
            epg_config_row = db.fetchone(
                "SELECT id FROM provider_epg_config WHERE provider_id = ?",
                (provider_id,),
            )

            # Get values from epg_window if available
            future_days = epg_window.future_days if epg_window else 7
            past_days = epg_window.past_days if epg_window else 7

            if epg_config_row:
                # Update existing EPG config with NEW dynamic values
                db.execute(
                    """
                    UPDATE provider_epg_config
                    SET future_days = ?,
                        past_days = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (future_days, past_days, now, epg_config_row[0]),
                )
                logger.debug(f"Updated EPG config for provider: {provider_name} (f={future_days}, p={past_days})")
            else:
                # Insert new EPG config
                db.execute(
                    """
                    INSERT INTO provider_epg_config (
                        provider_id,
                        future_days,
                        past_days,
                        chunk_hours,
                        max_requests_per_second,
                        max_concurrent_channels,
                        max_retries,
                        timeout_seconds,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, 24, 5.0, 3, 3, 30, ?, ?)
                    """,
                    (provider_id, future_days, past_days, now, now),
                )
                logger.debug(f"Created EPG config for provider: {provider_name} (f={future_days}, p={past_days})")

        return ultimate_provider_id

    async def _process_channel(
            self,
            provider_id: int,
            provider_name: str,
            channel,  # Now accepts UltimateBackendChannel dataclass
    ) -> Optional[int]:
        """
        Process a single channel: create logical channel and mapping.
        Only updates when values actually change.
        """
        db = get_db()

        ultimate_channel_id = channel.id
        channel_name = channel.name
        channel_logo = channel.logo_url
        channel_number = channel.channel_number
        catchup_hours = channel.catchup_hours
        live_id = channel.live_id
        stream_uid = channel.stream_uid

        if not ultimate_channel_id or not channel_name:
            logger.warning(f"Invalid channel data (missing Id or Name): {channel}")
            return None

        # Get or create logical channel
        logical_name = f"{provider_name}:{ultimate_channel_id}"

        # First, try to get existing channel
        existing_channel = self.epg_service.get_channel_by_name(logical_name)

        if existing_channel:
            # Check if values actually changed before updating
            needs_update = False

            if existing_channel.display_name != channel_name:
                logger.debug(
                    f"Channel {logical_name} display_name changed: '{existing_channel.display_name}' -> '{channel_name}'")
                needs_update = True

            if existing_channel.icon_url != channel_logo:
                logger.debug(
                    f"Channel {logical_name} icon_url changed: '{existing_channel.icon_url}' -> '{channel_logo}'")
                needs_update = True

            if needs_update:
                db.execute(
                    """
                    UPDATE channels 
                    SET display_name = ?, icon_url = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (channel_name, channel_logo, existing_channel.id)
                )
                logger.info(f"Updated channel {logical_name}")
            else:
                logger.debug(f"Channel {logical_name} unchanged, skipping update")

            channel_obj = existing_channel
        else:
            # Create new channel
            channel_obj = self.epg_service.create_channel(
                name=logical_name,
                display_name=channel_name,
                icon_url=channel_logo,
            )
            logger.info(f"Created new channel {logical_name}")

        # Check if channel already exists in ultimate_channels
        row = db.fetchone(
            """
            SELECT id, channel_name, channel_number, logo_url, catchup_hours, live_id, stream_uid
            FROM ultimate_channels
            WHERE ultimate_provider_id = ? AND ultimate_channel_id = ?
            """,
            (provider_id, ultimate_channel_id),
        )

        if row:
            ultimate_channel_db_id = row[0]

            # Build dynamic update only for changed fields
            updates = []
            params = []

            if row[1] != channel_name:
                updates.append("channel_name = ?")
                params.append(channel_name)
                logger.debug(f"ultimate_channel {ultimate_channel_id} name changed: '{row[1]}' -> '{channel_name}'")

            if row[2] != channel_number:
                updates.append("channel_number = ?")
                params.append(channel_number)
                logger.debug(f"ultimate_channel {ultimate_channel_id} number changed: {row[2]} -> {channel_number}")

            if row[3] != channel_logo:
                updates.append("logo_url = ?")
                params.append(channel_logo)
                logger.debug(f"ultimate_channel {ultimate_channel_id} logo changed")

            if row[4] != catchup_hours:
                updates.append("catchup_hours = ?")
                params.append(catchup_hours)

            if row[5] != live_id:
                updates.append("live_id = ?")
                params.append(live_id)

            if row[6] != stream_uid:
                updates.append("stream_uid = ?")
                params.append(stream_uid)

            if updates:
                updates.append("updated_at = ?")
                params.append(datetime.utcnow().isoformat())
                params.append(ultimate_channel_db_id)

                db.execute(
                    f"UPDATE ultimate_channels SET {', '.join(updates)} WHERE id = ?",
                    tuple(params)
                )
                logger.info(f"Updated {len(updates) - 1} fields for ultimate_channel {ultimate_channel_id}")
            else:
                logger.debug(f"ultimate_channel {ultimate_channel_id} unchanged, skipping update")
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
                    channel_logo,
                    catchup_hours,
                    live_id,
                    stream_uid,
                ),
            )
            ultimate_channel_db_id = cursor.lastrowid
            logger.info(f"Created ultimate_channel entry: ID={ultimate_channel_db_id}")

        # Ensure mapping exists (only if missing)
        existing_mapping = db.fetchone(
            """
            SELECT id FROM ultimate_channel_mappings 
            WHERE ultimate_channel_id = ? AND channel_id = ?
            """,
            (ultimate_channel_db_id, channel_obj.id),
        )

        if not existing_mapping:
            db.execute(
                """
                INSERT INTO ultimate_channel_mappings (ultimate_channel_id, channel_id)
                VALUES (?, ?)
                """,
                (ultimate_channel_db_id, channel_obj.id),
            )
            logger.info(f"Created mapping: ultimate_channel_id={ultimate_channel_db_id} -> channel_id={channel_obj.id}")

        # Ensure import state exists (only if missing)
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

        return channel_obj.id

    @staticmethod
    async def _update_discovery_timestamp(instance_id: int):
        """Update last_discovered_at for all providers in this instance."""
        db = get_db()
        now = datetime.utcnow().isoformat()

        db.execute(
            "UPDATE ultimate_providers SET last_discovered_at = ? WHERE instance_id = ?",
            (now, instance_id),
        )
