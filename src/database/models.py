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
            "created_at": to_utc_isoformat(self.created_at),
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
    def from_db_row(cls, row) -> "ChannelMapping":
        """Create ChannelMapping from database row."""
        return cls(
            id=row["id"],
            provider_id=row["provider_id"],
            provider_channel_id=row["provider_channel_id"],
            channel_id=row["channel_id"],
            created_at=(
                datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
            ),
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
    def from_db_row(cls, row) -> "ChannelAlias":
        """Create ChannelAlias from database row."""
        return cls(
            id=row["id"],
            channel_id=row["channel_id"],
            alias=row["alias"],
            alias_type=row["alias_type"],
            created_at=(
                datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
            ),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "id": self.id,
            "channel_id": self.channel_id,
            "alias": self.alias,
            "alias_type": self.alias_type,
            "created_at": to_utc_isoformat(self.created_at),
        }

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
    actors: Optional[list] = None
    directors: Optional[list] = None
    presenters: Optional[list] = None
    writers: Optional[list] = None
    producers: Optional[list] = None
    icon_url: Optional[str] = None
    production_year: Optional[str] = None
    country: Optional[str] = None
    # Ultimate Backend fields (NULL for other sources)
    ultimate_epg_id: Optional[int] = None
    schedule_id: Optional[str] = None
    genre_description: Optional[str] = None
    genre_dvb: Optional[int] = None
    categories: Optional[list] = None
    season_num: Optional[int] = None
    director: Optional[str] = None
    producer: Optional[str] = None
    year: Optional[int] = None
    star_rating: Optional[int] = None
    thumbnail_url: Optional[str] = None
    original_title: Optional[str] = None
    epg_flags: Optional[int] = None
    has_episode_info: Optional[int] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row) -> "Program":
        """Create Program from database row."""

        def _safe(key, default=None):
            try:
                return row[key]
            except (IndexError, KeyError):
                return default

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
            presenters=cls._parse_json_field(_safe("presenters")),
            writers=cls._parse_json_field(_safe("writers")),
            producers=cls._parse_json_field(_safe("producers")),
            icon_url=row["icon_url"],
            production_year=_safe("production_year"),
            country=_safe("country"),
            # Ultimate Backend fields — use _safe() so rows from other sources
            # that predate v3 don't raise KeyError
            ultimate_epg_id=_safe("ultimate_epg_id"),
            schedule_id=_safe("schedule_id"),
            genre_description=_safe("genre_description"),
            genre_dvb=_safe("genre_dvb"),
            categories=cls._parse_json_field(_safe("categories")),
            season_num=_safe("season_num"),
            director=_safe("director"),
            producer=_safe("producer"),
            year=_safe("year"),
            star_rating=_safe("star_rating"),
            thumbnail_url=_safe("thumbnail_url"),
            original_title=_safe("original_title"),
            epg_flags=_safe("epg_flags"),
            has_episode_info=_safe("has_episode_info"),
            created_at=(
                datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
            ),
        )

    @staticmethod
    def _parse_json_field(value) -> Optional[list]:
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
            if isinstance(value, str):
                return [item.strip() for item in value.split(",") if item.strip()]
            return None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
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
            "actors": self.actors or [],
            "directors": self.directors or [],
            "presenters": self.presenters or [],
            "writers": self.writers or [],
            "producers": self.producers or [],
            "icon_url": self.icon_url,
            "production_year": self.production_year,
            "country": self.country,
            "created_at": to_utc_isoformat(self.created_at),
        }

        # Include Ultimate Backend fields only when present so the API
        # response stays clean for programs from other sources.
        if self.ultimate_epg_id is not None:
            result["ultimate_epg_id"] = self.ultimate_epg_id
        if self.original_title is not None:
            result["original_title"] = self.original_title
        if self.genre_description is not None:
            result["genre_description"] = self.genre_description
        if self.genre_dvb is not None:
            result["genre_dvb"] = self.genre_dvb
        if self.categories is not None:
            result["categories"] = self.categories
        if self.season_num is not None:
            result["season_num"] = self.season_num
        if self.year is not None:
            result["year"] = self.year
        if self.star_rating is not None:
            result["star_rating"] = self.star_rating
        if self.thumbnail_url is not None:
            result["thumbnail_url"] = self.thumbnail_url
        if self.epg_flags is not None:
            result["epg_flags"] = self.epg_flags

        return result


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
    def from_db_row(cls, row) -> "Provider":
        """Create Provider from database row."""
        return cls(
            id=row["id"],
            name=row["name"],
            xmltv_url=row["xmltv_url"],
            enabled=bool(row["enabled"]),
            created_at=(
                datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
            ),
            updated_at=(
                datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None
            ),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "xmltv_url": self.xmltv_url,
            "enabled": self.enabled,
            "created_at": to_utc_isoformat(self.created_at),
            "updated_at": to_utc_isoformat(self.updated_at),
        }


@dataclass
class ImportLog:
    """Tracks import operations."""

    id: Optional[int] = None
    provider_id: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: str = "running"
    programs_imported: int = 0
    programs_skipped: int = 0
    error_message: Optional[str] = None

    @classmethod
    def from_db_row(cls, row) -> "ImportLog":
        """Create ImportLog from database row."""
        return cls(
            id=row["id"],
            provider_id=row["provider_id"],
            started_at=(
                datetime.fromisoformat(row["started_at"]) if row["started_at"] else None
            ),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
            status=row["status"],
            programs_imported=row["programs_imported"],
            programs_skipped=row["programs_skipped"],
            error_message=row["error_message"],
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "started_at": to_utc_isoformat(self.started_at),
            "completed_at": to_utc_isoformat(self.completed_at),
            "status": self.status,
            "programs_imported": self.programs_imported,
            "programs_skipped": self.programs_skipped,
            "error_message": self.error_message,
        }