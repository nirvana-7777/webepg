"""
Data models for Ultimate Backend API responses.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


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

    # NOTE: The real API uses string IDs like "Zf4PyxjqUvbe" (magenta2),
    # not numeric IDs. The model already stores id as str, which is correct.
    id: str
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
        # Safely coerce channel_number: missing, null, or non-integer all → 0
        raw_number = data.get("ChannelNumber", data.get("channel_number"))
        try:
            channel_number = int(raw_number) if raw_number is not None else 0
        except (ValueError, TypeError):
            channel_number = 0

        # Safely coerce catchup_hours: fall back to provider-level value if present
        raw_catchup = data.get("CatchupHours", data.get("catchup_hours"))
        try:
            catchup_hours = int(raw_catchup) if raw_catchup is not None else 168
        except (ValueError, TypeError):
            catchup_hours = 168

        return cls(
            id=str(data.get("Id", data.get("id", ""))),
            name=data.get("Name", data.get("name", "")),
            logo_url=data.get("LogoUrl", data.get("logo_url")),
            channel_number=channel_number,
            catchup_hours=catchup_hours,
            live_id=data.get("LiveId", data.get("live_id")),
            stream_uid=data.get("StreamUid", data.get("stream_uid")),
            country=data.get("Country", data.get("country")),
            language=data.get("Language", data.get("language")),
        )


@dataclass
class EPGWindow:
    """EPG window information from Ultimate Backend API."""

    past_days: int = 7
    future_days: int = 7
    implements_epg: bool = True

    @classmethod
    def from_api_response(cls, data: Optional[dict]) -> "EPGWindow":
        if not data:
            return cls()
        return cls(
            past_days=data.get("past_days", 7),
            future_days=data.get("future_days", 7),
            implements_epg=data.get("implements_epg", True),
        )


@dataclass
class UltimateBackendChannelList:
    """Wrapped channel list with provider-level EPG info."""

    provider: str
    country: Optional[str]
    catchup_window_hours: int
    epg_window: EPGWindow
    channels: List[UltimateBackendChannel]

    @classmethod
    def from_api_response(cls, data: dict) -> "UltimateBackendChannelList":
        epg_window_data = data.get("epg_window")
        channels_data = data.get("channels", [])

        return cls(
            provider=data.get("provider", ""),
            country=data.get("country"),
            catchup_window_hours=data.get("catchup_window_hours", 0),
            epg_window=EPGWindow.from_api_response(epg_window_data),
            channels=[
                UltimateBackendChannel.from_api_response(c) for c in channels_data
            ],
        )


@dataclass
class UltimateBackendProgram:
    """Program from Ultimate Backend EPG API."""

    epg_id: Optional[int] = None
    schedule_id: str = ""
    title: str = ""
    start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    plot: Optional[str] = None
    original_title: Optional[str] = None
    episode_title: Optional[str] = None
    genre: Optional[str] = None
    categories: List[str] = field(default_factory=list)
    season_num: Optional[int] = None
    episode_num: Optional[int] = None
    has_episode_info: bool = False
    director: Optional[str] = None
    directors: List[str] = field(default_factory=list)
    cast: List[str] = field(default_factory=list)
    producer: Optional[str] = None
    producers: List[str] = field(default_factory=list)
    writers: List[str] = field(default_factory=list)
    presenters: List[str] = field(default_factory=list)
    language: Optional[str] = None
    year: Optional[int] = None
    rating: Optional[str] = None
    thumbnail: Optional[str] = None
    images: Optional[Dict] = None
    live_id: Optional[int] = None
    live_name: Optional[str] = None
    content_id: Optional[int] = None
    genre_id: Optional[int] = None
    category_ids: List[int] = field(default_factory=list)

    @classmethod
    def _parse_datetime(cls, value: Any) -> Optional[datetime]:
        """
        Parse datetime value which could be an ISO 8601 string or an epoch integer/float.
        Normalises to UTC-naive datetime for database storage.
        """
        if value is None or value == "":
            return None

        if isinstance(value, (int, float)):
            # Handle epoch timestamp
            return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)

        try:
            # Handle ISO string
            dt = datetime.fromisoformat(str(value))
            if dt.tzinfo is not None:
                # Convert to UTC then strip tzinfo
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except (ValueError, TypeError) as e:
            raise ValueError(f"Cannot parse datetime '{value}': {e}") from e

    @classmethod
    def from_api_response(cls, data: dict) -> "UltimateBackendProgram":
        """Parse API response into model."""
        start = cls._parse_datetime(data.get("start"))
        end = cls._parse_datetime(data.get("end"))

        if start is None or end is None:
            raise ValueError(
                f"Program {data.get('epg_id', data.get('program_id'))} missing start/end time"
            )

        # Map person lists, handling plural/singular variations
        directors = data.get("directors") or []
        if not directors and data.get("director"):
            directors = [data.get("director")]

        producers = data.get("producers") or []
        if not producers and data.get("producer"):
            producers = [data.get("producer")]

        presenters = data.get("presenter") or data.get("presenters") or []
        if isinstance(presenters, str):
            presenters = [presenters]

        writers = data.get("writer") or data.get("writers") or []
        if isinstance(writers, str):
            writers = [writers]

        return cls(
            epg_id=data.get("epg_id"),
            schedule_id=str(data.get("schedule_id", data.get("program_id", ""))),
            title=data.get("title", ""),
            start=start,
            end=end,
            plot=data.get("plot", data.get("description")) or None,
            original_title=data.get("original_title") or None,
            episode_title=data.get("episode_title", data.get("episode_name")) or None,
            genre=data.get("genre", data.get("genre_description")) or None,
            categories=data.get("categories") or [],
            season_num=data.get("season_num", data.get("season_number")),
            episode_num=data.get("episode_num", data.get("episode_number")),
            has_episode_info=bool(data.get("has_episode_info", False)),
            director=data.get("director") or (directors[0] if directors else None),
            directors=directors,
            cast=data.get("cast") or [],
            producer=data.get("producer") or (producers[0] if producers else None),
            producers=producers,
            writers=writers,
            presenters=presenters,
            language=data.get("language"),
            year=data.get("year"),
            rating=str(data.get("rating", data.get("parental_rating_code", "")))
            or None,
            thumbnail=data.get("thumbnail", data.get("image")) or None,
            images=data.get("images") or None,
            live_id=data.get("live_id"),
            live_name=data.get("live_name") or None,
            content_id=data.get("content_id"),
            genre_id=data.get("genre_id"),
            category_ids=data.get("category_ids") or [],
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
            "directors": json.dumps(self.directors) if self.directors else None,
            "actors": json.dumps(self.cast) if self.cast else None,
            "producer": self.producer,
            "producers": json.dumps(self.producers) if self.producers else None,
            "writers": json.dumps(self.writers) if self.writers else None,
            "presenters": json.dumps(self.presenters) if self.presenters else None,
            "production_year": str(self.year) if self.year else None,
            "language": self.language,
            "rating": self.rating,
            "thumbnail_url": self.thumbnail,
            "images": json.dumps(self.images) if self.images else None,
        }
