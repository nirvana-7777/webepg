"""
EPG service for querying program data.
"""

import logging
from datetime import datetime
from typing import List, Optional

from ..database.connection import get_db
from ..database.models import Channel, ChannelAlias, Program

logger = logging.getLogger(__name__)


class EPGService:
    """Service for EPG data queries."""

    def get_programs(
        self, channel_id: int, start: datetime, end: datetime
    ) -> List[Program]:
        """
        Get programs for a channel within a time range.

        Args:
            channel_id: Logical channel ID
            start: Start of time range
            end: End of time range

        Returns:
            List of Program objects
        """
        db = get_db()

        sql = """
            SELECT 
                id, channel_id, provider_id, start_time, end_time,
                title, subtitle, description, category, episode_num,
                rating, actors, directors, icon_url, created_at
            FROM programs
            WHERE channel_id = ?
              AND start_time < ?
              AND end_time > ?
            ORDER BY start_time ASC
        """

        # Convert datetime to ISO format for SQLite
        start_str = start.isoformat()
        end_str = end.isoformat()

        try:
            rows = db.fetchall(sql, (channel_id, end_str, start_str))
            programs = [Program.from_db_row(tuple(row)) for row in rows]

            logger.debug(
                f"Found {len(programs)} programs for channel {channel_id} "
                f"between {start} and {end}"
            )

            return programs
        except Exception as e:
            logger.error(f"Error fetching programs: {e}")
            raise

    def get_channel(self, channel_id: int) -> Optional[Channel]:
        """
        Get channel by ID.

        Args:
            channel_id: Channel ID

        Returns:
            Channel object or None if not found
        """
        db = get_db()

        sql = """
            SELECT id, name, display_name, icon_url, created_at
            FROM channels
            WHERE id = ?
        """

        try:
            row = db.fetchone(sql, (channel_id,))
            if row:
                return Channel.from_db_row(tuple(row))
            return None
        except Exception as e:
            logger.error(f"Error fetching channel {channel_id}: {e}")
            raise

    def get_channel_by_name(self, name: str) -> Optional[Channel]:
        """
        Get channel by name.

        Args:
            name: Channel name

        Returns:
            Channel object or None if not found
        """
        db = get_db()

        sql = """
            SELECT id, name, display_name, icon_url, created_at
            FROM channels
            WHERE name = ?
        """

        try:
            row = db.fetchone(sql, (name,))
            if row:
                return Channel.from_db_row(tuple(row))
            return None
        except Exception as e:
            logger.error(f"Error fetching channel by name '{name}': {e}")
            raise

    def list_channels(self) -> List[Channel]:
        """
        List all channels.

        Returns:
            List of Channel objects
        """
        db = get_db()

        sql = """
            SELECT id, name, display_name, icon_url, created_at
            FROM channels
            ORDER BY display_name ASC
        """

        try:
            rows = db.fetchall(sql)
            channels = [Channel.from_db_row(tuple(row)) for row in rows]

            logger.debug(f"Listed {len(channels)} channels")

            return channels
        except Exception as e:
            logger.error(f"Error listing channels: {e}")
            raise

    def create_channel(
        self, name: str, display_name: str, icon_url: Optional[str] = None
    ) -> Channel:
        """
        Create a new channel.

        Args:
            name: Unique channel name (identifier)
            display_name: Human-readable display name
            icon_url: Optional channel icon URL

        Returns:
            Created Channel object
        """
        db = get_db()

        sql = """
            INSERT INTO channels (name, display_name, icon_url)
            VALUES (?, ?, ?)
        """

        try:
            with db.get_cursor() as cursor:
                cursor.execute(sql, (name, display_name, icon_url))
                channel_id = cursor.lastrowid

            logger.info(f"Created channel: {name} (ID: {channel_id})")

            # Fetch and return the created channel
            return self.get_channel(channel_id)
        except Exception as e:
            logger.error(f"Error creating channel '{name}': {e}")
            raise

    def get_or_create_channel(
        self, name: str, display_name: str, icon_url: Optional[str] = None
    ) -> Channel:
        """
        Get existing channel or create if it doesn't exist.

        Args:
            name: Unique channel name (identifier)
            display_name: Human-readable display name
            icon_url: Optional channel icon URL

        Returns:
            Channel object
        """
        channel = self.get_channel_by_name(name)
        if channel:
            return channel

        return self.create_channel(name, display_name, icon_url)

    def get_channel_by_id_or_alias(self, identifier: str) -> Optional[Channel]:
        """
        Get channel by numeric ID, name, or alias.

        This method tries multiple lookup strategies:
        1. Numeric ID (if identifier is a number)
        2. Channel name (exact match)
        3. Channel alias (from channel_aliases table)

        Args:
            identifier: Channel ID (numeric), name, or alias

        Returns:
            Channel object or None if not found
        """
        # Try numeric ID first
        if identifier.isdigit():
            channel = self.get_channel(int(identifier))
            if channel:
                return channel

        # Try channel name
        channel = self.get_channel_by_name(identifier)
        if channel:
            return channel

        # Try alias
        channel = self.get_channel_by_alias(identifier)
        return channel

    def get_channel_by_alias(self, alias: str) -> Optional[Channel]:
        """
        Get channel by alias.

        Args:
            alias: Channel alias

        Returns:
            Channel object or None if not found
        """
        db = get_db()

        sql = """
            SELECT c.id, c.name, c.display_name, c.icon_url, c.created_at
            FROM channels c
            JOIN channel_aliases ca ON c.id = ca.channel_id
            WHERE ca.alias = ?
        """

        try:
            row = db.fetchone(sql, (alias,))
            if row:
                return Channel.from_db_row(tuple(row))
            return None
        except Exception as e:
            logger.error(f"Error fetching channel by alias '{alias}': {e}")
            raise

    def create_channel_alias(
        self, channel_id: int, alias: str, alias_type: Optional[str] = None
    ) -> "ChannelAlias":
        """
        Create an alias for a channel.

        Args:
            channel_id: Logical channel ID
            alias: Alias string (must be unique across all aliases)
            alias_type: Optional type classification (e.g., 'provider', 'epg_id', 'custom')

        Returns:
            Created ChannelAlias object
        """

        db = get_db()

        sql = """
            INSERT INTO channel_aliases (channel_id, alias, alias_type)
            VALUES (?, ?, ?)
        """

        try:
            with db.get_cursor() as cursor:
                cursor.execute(sql, (channel_id, alias, alias_type))
                alias_id = cursor.lastrowid

            logger.info(f"Created alias '{alias}' for channel {channel_id}")

            # Fetch and return the created alias
            return self.get_channel_alias(alias_id)
        except Exception as e:
            logger.error(
                f"Error creating alias '{alias}' for channel {channel_id}: {e}"
            )
            raise

    def get_channel_alias(self, alias_id: int) -> Optional["ChannelAlias"]:
        """Get channel alias by ID."""
        from ..database.models import ChannelAlias

        db = get_db()

        sql = """
            SELECT id, channel_id, alias, alias_type, created_at
            FROM channel_aliases
            WHERE id = ?
        """

        row = db.fetchone(sql, (alias_id,))
        if row:
            return ChannelAlias.from_db_row(tuple(row))
        return None

    def list_channel_aliases(self, channel_id: int) -> List["ChannelAlias"]:
        """
        List all aliases for a channel.

        Args:
            channel_id: Logical channel ID

        Returns:
            List of ChannelAlias objects
        """
        from ..database.models import ChannelAlias

        db = get_db()

        sql = """
            SELECT id, channel_id, alias, alias_type, created_at
            FROM channel_aliases
            WHERE channel_id = ?
            ORDER BY created_at ASC
        """

        try:
            rows = db.fetchall(sql, (channel_id,))
            aliases = [ChannelAlias.from_db_row(tuple(row)) for row in rows]

            logger.debug(f"Found {len(aliases)} aliases for channel {channel_id}")

            return aliases
        except Exception as e:
            logger.error(f"Error listing aliases for channel {channel_id}: {e}")
            raise

    def delete_channel_alias(self, alias_id: int) -> bool:
        """
        Delete a channel alias.

        Args:
            alias_id: Alias ID

        Returns:
            True if deleted, False if not found
        """
        db = get_db()

        sql = "DELETE FROM channel_aliases WHERE id = ?"

        try:
            with db.get_cursor() as cursor:
                cursor.execute(sql, (alias_id,))
                deleted = cursor.rowcount > 0

            if deleted:
                logger.info(f"Deleted alias {alias_id}")
            else:
                logger.warning(f"Alias {alias_id} not found")

            return deleted
        except Exception as e:
            logger.error(f"Error deleting alias {alias_id}: {e}")
            raise
