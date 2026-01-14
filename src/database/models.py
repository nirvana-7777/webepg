# models.py - REMOVE the duplicate ChannelAlias class at the top:

"""
Data models for EPG service.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..utils.time_utils import to_utc_isoformat


@dataclass
class Channel:
    """Logical channel (user-facing)."""

    id: Optional[int] = None
    name: str = ""
    display_name: str = ""
    icon_url: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row) -> "Channel":
        """Create Channel from database row."""
        return cls(
            id=row["id"],
            name=row["name"],
            display_name=row["display_name"],
            icon_url=row["icon_url"],
            created_at=(
                datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
            ),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "icon_url": self.icon_url,
            "created_at": to_utc_isoformat(self.created_at),  # Use helper
        }


@dataclass
class ChannelMapping:
    """Maps provider channel IDs to logical channels."""

    id: Optional[int] = None
    provider_id: int = 0
    provider_channel_id: str = ""
    channel_id: int = 0
    created_at: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row) -> "ChannelMapping":  # Remove : tuple
        """Create ChannelMapping from database row."""
        return cls(
            id=row["id"],  # Change from row[0]
            provider_id=row["provider_id"],  # Change from row[1]
            provider_channel_id=row["provider_channel_id"],  # Change from row[2]
            channel_id=row["channel_id"],  # Change from row[3]
            created_at=(
                datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
            ),  # Change from row[4]
        )


# KEEP THIS ONE - The complete ChannelAlias class with methods
@dataclass
class ChannelAlias:
    """Channel alias for flexible API access."""

    id: Optional[int] = None
    channel_id: int = 0
    alias: str = ""
    alias_type: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row) -> "ChannelAlias":  # Remove : tuple
        """Create ChannelAlias from database row."""
        return cls(
            id=row["id"],  # Change from row[0]
            channel_id=row["channel_id"],  # Change from row[1]
            alias=row["alias"],  # Change from row[2]
            alias_type=row["alias_type"],  # Change from row[3]
            created_at=(
                datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
            ),  # Change from row[4]
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "id": self.id,
            "channel_id": self.channel_id,
            "alias": self.alias,
            "alias_type": self.alias_type,
            "created_at": to_utc_isoformat(self.created_at),  # Use helper
        }

        # Add any additional attributes that might have been set
        if hasattr(self, "channel_name"):
            result["channel_name"] = self.channel_name

        if hasattr(self, "channel_display_name"):
            result["channel_display_name"] = self.channel_display_name

        return result


@dataclass
class Program:
    """EPG program data."""

    id: Optional[int] = None
    channel_id: int = 0
    provider_id: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    title: str = ""
    subtitle: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    episode_num: Optional[str] = None
    rating: Optional[str] = None
    actors: Optional[list] = None  # Changed from str to list
    directors: Optional[list] = None  # Changed from str to list
    presenters: Optional[list] = None  # New field
    writers: Optional[list] = None  # New field
    producers: Optional[list] = None  # New field
    icon_url: Optional[str] = None
    production_year: Optional[str] = None  # New field
    country: Optional[str] = None  # New field
    created_at: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row) -> "Program":
        """Create Program from database row.

        Args:
            row: Database row (sqlite3.Row object or dict-like)
        """
        return cls(
            id=row["id"],
            channel_id=row["channel_id"],
            provider_id=row["provider_id"],
            start_time=(
                datetime.fromisoformat(row["start_time"]) if row["start_time"] else None
            ),
            end_time=(
                datetime.fromisoformat(row["end_time"]) if row["end_time"] else None
            ),
            title=row["title"],
            subtitle=row["subtitle"],
            description=row["description"],
            category=row["category"],
            episode_num=row["episode_num"],
            rating=row["rating"],
            actors=cls._parse_json_field(row["actors"]),
            directors=cls._parse_json_field(row["directors"]),
            presenters=cls._parse_json_field(row["presenters"]),
            writers=cls._parse_json_field(row["writers"]),
            producers=cls._parse_json_field(row["producers"]),
            icon_url=row["icon_url"],
            production_year=row["production_year"],
            country=row["country"],
            created_at=(
                datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
            ),
        )

    @staticmethod
    def _parse_json_field(value: str) -> Optional[list]:
        """Parse JSON string field, return list if valid, None otherwise."""
        if not value:
            return None
        try:
            import json

            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
            return [parsed] if parsed else None
        except (json.JSONDecodeError, TypeError):
            # Fallback: if it's a comma-separated string, split it
            if isinstance(value, str):
                return [item.strip() for item in value.split(",") if item.strip()]
            return None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "provider_id": self.provider_id,
            "start_time": to_utc_isoformat(self.start_time),
            "end_time": to_utc_isoformat(self.end_time),
            "title": self.title,
            "subtitle": self.subtitle,
            "description": self.description,
            "category": self.category,
            "episode_num": self.episode_num,
            "rating": self.rating,
            "actors": self.actors or [],  # Ensure list
            "directors": self.directors or [],  # Ensure list
            "presenters": self.presenters or [],  # New field
            "writers": self.writers or [],  # New field
            "producers": self.producers or [],  # New field
            "icon_url": self.icon_url,
            "production_year": self.production_year,  # New field
            "country": self.country,  # New field
            "created_at": to_utc_isoformat(self.created_at),
        }


@dataclass
class Provider:
    """EPG data provider."""

    id: Optional[int] = None
    name: str = ""
    xmltv_url: str = ""
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row) -> "Provider":  # Remove : tuple
        """Create Provider from database row."""
        return cls(
            id=row["id"],  # Change from row[0]
            name=row["name"],  # Change from row[1]
            xmltv_url=row["xmltv_url"],  # Change from row[2]
            enabled=bool(row["enabled"]),  # Change from row[3]
            created_at=(
                datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
            ),  # Change from row[4]
            updated_at=(
                datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None
            ),  # Change from row[5]
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "xmltv_url": self.xmltv_url,
            "enabled": self.enabled,
            "created_at": to_utc_isoformat(self.created_at),  # Use helper
            "updated_at": to_utc_isoformat(self.updated_at),  # Use helper
        }


@dataclass
class ImportLog:
    """Tracks import operations."""

    id: Optional[int] = None
    provider_id: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: str = "running"  # 'running', 'success', 'failed'
    programs_imported: int = 0
    programs_skipped: int = 0
    error_message: Optional[str] = None

    @classmethod
    def from_db_row(cls, row) -> "ImportLog":  # Remove : tuple
        """Create ImportLog from database row."""
        return cls(
            id=row["id"],  # Change from row[0]
            provider_id=row["provider_id"],  # Change from row[1]
            started_at=(
                datetime.fromisoformat(row["started_at"]) if row["started_at"] else None
            ),  # Change from row[2]
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
            # Change from row[3]
            status=row["status"],  # Change from row[4]
            programs_imported=row["programs_imported"],  # Change from row[5]
            programs_skipped=row["programs_skipped"],  # Change from row[6]
            error_message=row["error_message"],  # Change from row[7]
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "started_at": to_utc_isoformat(self.started_at),  # Use helper
            "completed_at": to_utc_isoformat(self.completed_at),  # Use helper
            "status": self.status,
            "programs_imported": self.programs_imported,
            "programs_skipped": self.programs_skipped,
            "error_message": self.error_message,
        }
