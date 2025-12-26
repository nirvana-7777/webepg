"""
Provider service for managing EPG data providers.
"""
import logging
from typing import List, Optional
from datetime import datetime

from ..database.connection import get_db
from ..database.models import Provider, ChannelMapping

logger = logging.getLogger(__name__)


class ProviderService:
    """Service for provider management."""

    def create_provider(self, name: str, xmltv_url: str) -> Provider:
        """
        Create a new EPG provider.

        Args:
            name: Provider name (must be unique)
            xmltv_url: URL to XMLTV feed

        Returns:
            Created Provider object
        """
        db = get_db()

        sql = """
              INSERT INTO providers (name, xmltv_url)
              VALUES (?, ?) \
              """

        try:
            with db.get_cursor() as cursor:
                cursor.execute(sql, (name, xmltv_url))
                provider_id = cursor.lastrowid

            logger.info(f"Created provider: {name} (ID: {provider_id})")

            return self.get_provider(provider_id)
        except Exception as e:
            logger.error(f"Error creating provider '{name}': {e}")
            raise

    def update_provider(
            self,
            provider_id: int,
            name: Optional[str] = None,
            xmltv_url: Optional[str] = None,
            enabled: Optional[bool] = None
    ) -> Provider:
        """
        Update an existing provider.

        Args:
            provider_id: Provider ID
            name: New name (optional)
            xmltv_url: New XMLTV URL (optional)
            enabled: Enable/disable provider (optional)

        Returns:
            Updated Provider object
        """
        db = get_db()

        # Build dynamic UPDATE query
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)

        if xmltv_url is not None:
            updates.append("xmltv_url = ?")
            params.append(xmltv_url)

        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)

        if not updates:
            # No updates requested, just return current state
            return self.get_provider(provider_id)

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(provider_id)

        sql = f"UPDATE providers SET {', '.join(updates)} WHERE id = ?"

        try:
            db.execute(sql, tuple(params))
            logger.info(f"Updated provider {provider_id}")

            return self.get_provider(provider_id)
        except Exception as e:
            logger.error(f"Error updating provider {provider_id}: {e}")
            raise

    def delete_provider(self, provider_id: int) -> bool:
        """
        Delete a provider and all associated data.

        Args:
            provider_id: Provider ID

        Returns:
            True if deleted, False if not found
        """
        db = get_db()

        sql = "DELETE FROM providers WHERE id = ?"

        try:
            with db.get_cursor() as cursor:
                cursor.execute(sql, (provider_id,))
                deleted = cursor.rowcount > 0

            if deleted:
                logger.info(f"Deleted provider {provider_id}")
            else:
                logger.warning(f"Provider {provider_id} not found")

            return deleted
        except Exception as e:
            logger.error(f"Error deleting provider {provider_id}: {e}")
            raise

    def get_provider(self, provider_id: int) -> Optional[Provider]:
        """
        Get provider by ID.

        Args:
            provider_id: Provider ID

        Returns:
            Provider object or None if not found
        """
        db = get_db()

        sql = """
              SELECT id, name, xmltv_url, enabled, created_at, updated_at
              FROM providers
              WHERE id = ? \
              """

        try:
            row = db.fetchone(sql, (provider_id,))
            if row:
                return Provider.from_db_row(tuple(row))
            return None
        except Exception as e:
            logger.error(f"Error fetching provider {provider_id}: {e}")
            raise

    def list_providers(self, enabled_only: bool = False) -> List[Provider]:
        """
        List all providers.

        Args:
            enabled_only: If True, only return enabled providers

        Returns:
            List of Provider objects
        """
        db = get_db()

        sql = """
              SELECT id, name, xmltv_url, enabled, created_at, updated_at
              FROM providers \
              """

        if enabled_only:
            sql += " WHERE enabled = 1"

        sql += " ORDER BY name ASC"

        try:
            rows = db.fetchall(sql)
            providers = [Provider.from_db_row(tuple(row)) for row in rows]

            logger.debug(f"Listed {len(providers)} providers (enabled_only={enabled_only})")

            return providers
        except Exception as e:
            logger.error(f"Error listing providers: {e}")
            raise

    def create_channel_mapping(
            self,
            provider_id: int,
            provider_channel_id: str,
            channel_id: int
    ) -> ChannelMapping:
        """
        Create a mapping between provider channel ID and logical channel.

        Args:
            provider_id: Provider ID
            provider_channel_id: Channel ID from provider's XMLTV
            channel_id: Logical channel ID

        Returns:
            Created ChannelMapping object
        """
        db = get_db()

        sql = """
              INSERT INTO channel_mappings (provider_id, provider_channel_id, channel_id)
              VALUES (?, ?, ?) \
              """

        try:
            with db.get_cursor() as cursor:
                cursor.execute(sql, (provider_id, provider_channel_id, channel_id))
                mapping_id = cursor.lastrowid

            logger.debug(
                f"Created channel mapping: provider={provider_id}, "
                f"provider_channel={provider_channel_id}, channel={channel_id}"
            )

            return self.get_channel_mapping(mapping_id)
        except Exception as e:
            logger.error(f"Error creating channel mapping: {e}")
            raise

    def get_channel_mapping(self, mapping_id: int) -> Optional[ChannelMapping]:
        """Get channel mapping by ID."""
        db = get_db()

        sql = """
              SELECT id, provider_id, provider_channel_id, channel_id, created_at
              FROM channel_mappings
              WHERE id = ? \
              """

        row = db.fetchone(sql, (mapping_id,))
        if row:
            return ChannelMapping.from_db_row(tuple(row))
        return None

    def get_channel_for_provider_channel(
            self,
            provider_id: int,
            provider_channel_id: str
    ) -> Optional[int]:
        """
        Get logical channel ID for a provider's channel ID.

        Args:
            provider_id: Provider ID
            provider_channel_id: Channel ID from provider's XMLTV

        Returns:
            Logical channel ID or None if no mapping exists
        """
        db = get_db()

        sql = """
              SELECT channel_id
              FROM channel_mappings
              WHERE provider_id = ? \
                AND provider_channel_id = ? \
              """

        try:
            row = db.fetchone(sql, (provider_id, provider_channel_id))
            return row[0] if row else None
        except Exception as e:
            logger.error(
                f"Error getting channel mapping for provider {provider_id}, "
                f"channel {provider_channel_id}: {e}"
            )
            raise