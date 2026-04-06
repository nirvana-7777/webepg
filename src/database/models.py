"""
Data models for Ultimate Backend API responses.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


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
    """
    Program from Ultimate Backend EPG API.

    Field mapping to EPGEntry (epg_models.py / Kodi PVR spec):
    ----------------------------------------------------------
    epg_id          → ultimate_epg_id (DB linkage, not an EPGEntry field)
    schedule_id     → schedule_id     (DB only)
    title           → title           ✓
    start           → start           (stored as ISO, served as Unix timestamp)
    end             → end             (stored as ISO, served as Unix timestamp)
    plot            → description     ✓
    original_title  → original_title  ✓  (was missing — now stored)
    episode_title   → episode_name    ✓  (stored as subtitle)
    genre (str)     → genre_description ✓ (API text → EPGGenre.USE_STRING path)
                    → genre_dvb = NULL   (numeric DVB code; populate via genre mapper)
    categories      → categories      (JSON array, extra metadata)
    season_num      → season_number   ✓
    episode_num     → episode_number  ✓
    has_episode_info→ epg_flags       IS_SERIES flag set when True
    director (str)  → directors       wrapped as JSON ["director"] for EPGEntry list field
    cast ([str])    → cast / actors   JSON array ✓
    producer (str)  → producer        stored as TEXT (no EPGEntry field)
    year (int)      → year            INTEGER ✓  (was TEXT production_year — fixed)
    rating (int)    → star_rating     INTEGER 0-10 ✓  (was TEXT — fixed)
    thumbnail       → icon / thumbnail_url ✓
    images          → images          JSON dict (extra metadata)
    """

    epg_id: int
    schedule_id: str
    title: str
    start: datetime
    end: datetime
    plot: Optional[str] = None
    original_title: Optional[str] = None
    episode_title: Optional[str] = None
    genre: Optional[str] = None          # Raw text from API → genre_description
    categories: List[str] = field(default_factory=list)
    season_num: Optional[int] = None
    episode_num: Optional[int] = None
    has_episode_info: bool = False
    director: Optional[str] = None       # Single string from API
    cast: List[str] = field(default_factory=list)
    producer: Optional[str] = None
    year: Optional[int] = None           # Production year as integer
    rating: Optional[int] = None         # Star rating 0-10
    thumbnail: Optional[str] = None
    images: Optional[Dict] = None
    live_id: Optional[int] = None
    live_name: Optional[str] = None
    content_id: Optional[int] = None
    genre_id: Optional[int] = None
    category_ids: List[int] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: dict) -> "UltimateBackendProgram":
        """Parse API response dict into model."""

        # Parse start/end — API may return ISO strings or Unix timestamps
        def _parse_dt(value) -> datetime:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value, tz=timezone.utc)
            return datetime.fromisoformat(str(value))

        return cls(
            epg_id=data.get("epg_id"),
            schedule_id=data.get("schedule_id", ""),
            title=data.get("title", ""),
            start=_parse_dt(data["start"]),
            end=_parse_dt(data["end"]),
            plot=data.get("plot"),
            original_title=data.get("original_title"),
            episode_title=data.get("episode_title"),
            genre=data.get("genre"),           # text string, e.g. "Drama"
            categories=data.get("categories", []),
            season_num=data.get("season_num"),
            episode_num=data.get("episode_num"),
            has_episode_info=bool(data.get("has_episode_info", False)),
            director=data.get("director"),     # single string
            cast=data.get("cast", []),
            producer=data.get("producer"),
            year=data.get("year"),             # keep as int
            rating=data.get("rating"),         # keep as int
            thumbnail=data.get("thumbnail"),
            images=data.get("images"),
            live_id=data.get("live_id"),
            live_name=data.get("live_name"),
            content_id=data.get("content_id"),
            genre_id=data.get("genre_id"),
            category_ids=data.get("category_ids", []),
        )

    def _compute_epg_flags(self) -> int:
        """
        Derive EPGFlags bitmask from available metadata.

        EPGFlags (from epg_models.py):
            IS_SERIES  = 0x01  — part of a series
            IS_NEW     = 0x02  — new episode
            IS_PREMIERE= 0x04  — premiere
            IS_FINALE  = 0x08  — finale
            IS_LIVE    = 0x10  — live broadcast
        """
        flags = 0
        if self.has_episode_info:
            flags |= 0x01  # IS_SERIES
        return flags

    def to_dict(self) -> dict:
        """
        Convert to flat dict for INSERT into the programs table.

        Column names match the v3 schema produced by migrate_v2_to_v3().
        """
        # Wrap single-string director into a JSON list so it matches
        # EPGEntry.directors: List[str] when read back out.
        directors_json: Optional[str] = None
        if self.director:
            directors_json = json.dumps([self.director])

        return {
            # Ultimate Backend linkage
            "ultimate_epg_id":  self.epg_id,
            "schedule_id":      self.schedule_id,

            # Core program fields (match base programs table columns)
            "title":            self.title,
            "start_time":       self.start.isoformat(),
            "end_time":         self.end.isoformat(),
            "description":      self.plot,
            "subtitle":         self.episode_title,   # EPGEntry.episode_name

            # Genre — text goes to genre_description; genre_dvb stays NULL
            # until an optional genre-mapping step resolves it to a DVB code.
            "genre_description": self.genre,
            "genre_dvb":        None,                 # populated by genre mapper later

            # Extra metadata
            "categories":       json.dumps(self.categories) if self.categories else None,

            # Episode info
            "season_num":       self.season_num,
            "episode_num":      self.episode_num,
            "has_episode_info": 1 if self.has_episode_info else 0,

            # People
            # directors → JSON list (EPGEntry.directors: List[str])
            "directors":        directors_json,
            "director":         self.director,        # raw scalar kept for reference
            "actors":           json.dumps(self.cast) if self.cast else None,
            "producer":         self.producer,

            # Year as INTEGER (EPGEntry.year: int)
            "year":             self.year,

            # Rating as INTEGER star_rating 0-10 (EPGEntry.star_rating: int)
            "star_rating":      self.rating,

            # Media
            "thumbnail_url":    self.thumbnail,       # EPGEntry.icon
            "images":           json.dumps(self.images) if self.images else None,

            # Previously-missing EPGEntry fields now stored
            "original_title":   self.original_title,
            "epg_flags":        self._compute_epg_flags(),
        }

    def to_epg_entry_dict(self, broadcast_id: int) -> dict:
        """
        Produce a dict shaped for EPGEntry / the Kodi C++ frontend.

        Call this when you need to hand data to the PVR layer rather than
        write it to the DB.  broadcast_id must be pre-computed by the caller
        using EPGEntry.encode_broadcast_id().
        """
        result: dict = {
            "broadcast_id": broadcast_id,
            "title":        self.title,
            "start":        int(self.start.timestamp()),
            "end":          int(self.end.timestamp()),
        }

        if self.plot:
            result["description"] = self.plot
        if self.original_title:
            result["original_title"] = self.original_title
        if self.episode_title:
            result["episode_name"] = self.episode_title
        if self.year:
            result["year"] = self.year
        if self.thumbnail:
            result["icon"] = self.thumbnail
        if self.cast:
            result["cast"] = self.cast
        if self.director:
            result["directors"] = [self.director]
        # writers not available from this API source
        if self.genre:
            # Text genre → use EPGGenre.USE_STRING (0xF0) path
            result["genre"] = 0xF0
            result["genre_description"] = self.genre
        if self.season_num is not None:
            result["season_number"] = self.season_num
        if self.episode_num is not None:
            result["episode_number"] = self.episode_num
        if self.rating is not None:
            result["star_rating"] = self.rating

        flags = self._compute_epg_flags()
        if flags:
            result["flags"] = flags

        return result