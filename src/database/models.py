"""
Data models for EPG service.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ChannelAlias:
    """Channel alias for flexible API access."""

    id: Optional[int] = None
    channel_id: int = 0
    alias: str = ""
    alias_type: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class Channel:
    """Logical channel (user-facing)."""

    id: Optional[int] = None
    name: str = ""
    display_name: str = ""
    icon_url: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row: tuple) -> "Channel":
        """Create Channel from database row."""
        return cls(
            id=row[0],
            name=row[1],
            display_name=row[2],
            icon_url=row[3],
            created_at=datetime.fromisoformat(row[4]) if row[4] else None,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "icon_url": self.icon_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
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
    def from_db_row(cls, row: tuple) -> "ChannelMapping":
        """Create ChannelMapping from database row."""
        return cls(
            id=row[0],
            provider_id=row[1],
            provider_channel_id=row[2],
            channel_id=row[3],
            created_at=datetime.fromisoformat(row[4]) if row[4] else None,
        )


@dataclass
class ChannelAlias:
    """Channel alias for flexible API access."""

    id: Optional[int] = None
    channel_id: int = 0
    alias: str = ""
    alias_type: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row: tuple) -> "ChannelAlias":
        """Create ChannelAlias from database row."""
        return cls(
            id=row[0],
            channel_id=row[1],
            alias=row[2],
            alias_type=row[3],
            created_at=datetime.fromisoformat(row[4]) if row[4] else None,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "id": self.id,
            "channel_id": self.channel_id,
            "alias": self.alias,
            "alias_type": self.alias_type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

        # Add any additional attributes that might have been set
        if hasattr(self, 'channel_name'):
            result["channel_name"] = self.channel_name

        if hasattr(self, 'channel_display_name'):
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
    actors: Optional[str] = None
    directors: Optional[str] = None
    icon_url: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row: tuple) -> "Program":
        """Create Program from database row."""
        return cls(
            id=row[0],
            channel_id=row[1],
            provider_id=row[2],
            start_time=datetime.fromisoformat(row[3]) if row[3] else None,
            end_time=datetime.fromisoformat(row[4]) if row[4] else None,
            title=row[5],
            subtitle=row[6],
            description=row[7],
            category=row[8],
            episode_num=row[9],
            rating=row[10],
            actors=row[11],
            directors=row[12],
            icon_url=row[13],
            created_at=datetime.fromisoformat(row[14]) if row[14] else None,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "provider_id": self.provider_id,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "title": self.title,
            "subtitle": self.subtitle,
            "description": self.description,
            "category": self.category,
            "episode_num": self.episode_num,
            "rating": self.rating,
            "actors": self.actors,
            "directors": self.directors,
            "icon_url": self.icon_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
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
    def from_db_row(cls, row: tuple) -> "ImportLog":
        """Create ImportLog from database row."""
        return cls(
            id=row[0],
            provider_id=row[1],
            started_at=datetime.fromisoformat(row[2]) if row[2] else None,
            completed_at=datetime.fromisoformat(row[3]) if row[3] else None,
            status=row[4],
            programs_imported=row[5],
            programs_skipped=row[6],
            error_message=row[7],
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "status": self.status,
            "programs_imported": self.programs_imported,
            "programs_skipped": self.programs_skipped,
            "error_message": self.error_message,
        }
