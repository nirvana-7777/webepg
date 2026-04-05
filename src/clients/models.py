"""
Data models for Ultimate Backend API responses.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class UltimateBackendProvider:
    """Provider from Ultimate Backend API."""
    name: str
    label: str
    country: Optional[str] = None
    logo: Optional[str] = None
    enabled: bool = True
    requires_credentials: bool = False

    @classmethod
    def from_api_response(cls, data: dict) -> "UltimateBackendProvider":
        return cls(
            name=data.get("name"),
            label=data.get("label", data.get("name")),
            country=data.get("country"),
            logo=data.get("logo"),
            enabled=data.get("enabled", True),
            requires_credentials=data.get("requires_credentials", False),
        )


@dataclass
class UltimateBackendChannel:
    """Channel from Ultimate Backend API."""
    id: str  # Numeric ID as string
    name: str
    logo_url: Optional[str] = None
    channel_number: int = 0
    catchup_hours: int = 168  # 7 days default
    live_id: Optional[int] = None
    stream_uid: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None

    @classmethod
    def from_api_response(cls, data: dict) -> "UltimateBackendChannel":
        return cls(
            id=str(data.get("Id", data.get("id", ""))),
            name=data.get("Name", data.get("name", "")),
            logo_url=data.get("LogoUrl", data.get("logo_url")),
            channel_number=data.get("ChannelNumber", 0),
            catchup_hours=data.get("CatchupHours", 168),
            live_id=data.get("LiveId"),
            stream_uid=data.get("StreamUid"),
            country=data.get("Country"),
            language=data.get("Language"),
        )


@dataclass
class UltimateBackendProgram:
    """Program from Ultimate Backend EPG API."""
    epg_id: int
    schedule_id: str
    title: str
    start: datetime
    end: datetime
    plot: Optional[str] = None
    original_title: Optional[str] = None
    episode_title: Optional[str] = None
    genre: Optional[str] = None
    categories: List[str] = field(default_factory=list)
    season_num: Optional[int] = None
    episode_num: Optional[int] = None
    has_episode_info: bool = False
    director: Optional[str] = None
    cast: List[str] = field(default_factory=list)
    producer: Optional[str] = None
    year: Optional[int] = None
    rating: Optional[int] = None
    thumbnail: Optional[str] = None
    images: Optional[Dict] = None
    live_id: Optional[int] = None
    live_name: Optional[str] = None
    content_id: Optional[int] = None
    genre_id: Optional[int] = None
    category_ids: List[int] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: dict) -> "UltimateBackendProgram":
        """Parse API response into model."""
        return cls(
            epg_id=data.get("epg_id"),
            schedule_id=data.get("schedule_id", ""),
            title=data.get("title", ""),
            start=datetime.fromisoformat(data.get("start", "")),
            end=datetime.fromisoformat(data.get("end", "")),
            plot=data.get("plot"),
            original_title=data.get("original_title"),
            episode_title=data.get("episode_title"),
            genre=data.get("genre"),
            categories=data.get("categories", []),
            season_num=data.get("season_num"),
            episode_num=data.get("episode_num"),
            has_episode_info=data.get("has_episode_info", False),
            director=data.get("director"),
            cast=data.get("cast", []),
            producer=data.get("producer"),
            year=data.get("year"),
            rating=data.get("rating"),
            thumbnail=data.get("thumbnail"),
            images=data.get("images"),
            live_id=data.get("live_id"),
            live_name=data.get("live_name"),
            content_id=data.get("content_id"),
            genre_id=data.get("genre_id"),
            category_ids=data.get("category_ids", []),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        import json

        return {
            "ultimate_epg_id": self.epg_id,
            "schedule_id": self.schedule_id,
            "title": self.title,
            "start_time": self.start.isoformat(),
            "end_time": self.end.isoformat(),
            "description": self.plot,
            "subtitle": self.episode_title,
            "category": self.genre,
            "categories": json.dumps(self.categories) if self.categories else None,
            "season_num": self.season_num,
            "episode_num": self.episode_num,
            "has_episode_info": 1 if self.has_episode_info else 0,
            "director": self.director,
            "actors": json.dumps(self.cast) if self.cast else None,
            "producer": self.producer,
            "production_year": str(self.year) if self.year else None,
            "rating": str(self.rating) if self.rating else None,
            "thumbnail_url": self.thumbnail,
            "images": json.dumps(self.images) if self.images else None,
        }