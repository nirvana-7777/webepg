"""
EPG service for querying program data.
"""
import logging
from datetime import datetime
from typing import List, Optional

from ..database.connection import get_db
from ..database.models import Channel, Program

logger = logging.getLogger(__name__)


class EPGService:
    """Service for EPG data queries."""

    def get_programs(
            self,
            channel_id: int,
            start: datetime,
            end: datetime
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
              SELECT id, \
                     channel_id, \
                     provider_id, \
                     start_time, \
                     end_time, \
                     title, \
                     subtitle, \
                     description, \
                     category, \
                     episode_num, \
                     rating, \
                     actors, \
                     directors, \
                     icon_url, \
                     created_at
              FROM programs
              WHERE channel_id = ?
                AND start_time < ?
                AND end_time > ?
              ORDER BY start_time ASC \
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
              WHERE id = ? \
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
              WHERE name = ? \
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
              ORDER BY display_name ASC \
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
            self,
            name: str,
            display_name: str,
            icon_url: Optional[str] = None
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
              VALUES (?, ?, ?) \
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
            self,
            name: str,
            display_name: str,
            icon_url: Optional[str] = None
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