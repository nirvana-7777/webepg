"""
XMLTV serializer for exporting EPG data to XMLTV format.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, BinaryIO
from xml.etree.ElementTree import Element, ElementTree, SubElement, tostring
from xml.dom import minidom

logger = logging.getLogger(__name__)


class XMLTVSerializer:
    """Serialize EPG data to XMLTV format."""

    # XMLTV namespace and DTD
    DOCTYPE = '<!DOCTYPE tv SYSTEM "xmltv.dtd">'

    @staticmethod
    def _format_datetime(dt: datetime) -> str:
        """
        Format datetime to XMLTV format: YYYYMMDDHHMMSS +0000 (UTC).

        Args:
            dt: datetime object (assumed UTC)

        Returns:
            XMLTV formatted datetime string
        """
        if dt.tzinfo is None:
            # Assume UTC if naive
            return f"{dt.strftime('%Y%m%d%H%M%S')} +0000"
        # Convert to UTC explicitly and format.
        # NOTE: dt.astimezone() with no argument converts to the *system
        # local* timezone, not UTC. That was the bug here: an aware UTC
        # datetime got silently shifted to local wall-clock time (CEST,
        # +2h) while still being labeled "+0000" below. Passing
        # timezone.utc explicitly is what actually guarantees UTC output
        # regardless of what tzinfo dt arrives with or what timezone the
        # host machine is running in.
        utc_dt = dt.astimezone(timezone.utc)
        return f"{utc_dt.strftime('%Y%m%d%H%M%S')} +0000"

    @staticmethod
    def _parse_json_field(value: Any) -> Optional[List]:
        """Parse JSON field from database."""
        if not value:
            return None
        try:
            import json

            if isinstance(value, str):
                return json.loads(value)
            return value
        except (json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def serialize_channel(channel: Dict) -> Element:
        """
        Serialize a channel to XMLTV <channel> element.

        Args:
            channel: Channel dict with id, name, display_name, icon_url

        Returns:
            XML Element
        """
        channel_elem = Element("channel", id=channel["id"])

        # Display name (required, at least one)
        display_name = SubElement(channel_elem, "display-name")
        display_name.text = channel.get("display_name", channel.get("name", ""))
        display_name.set("lang", "en")

        # Icon if available
        if channel.get("icon_url"):
            icon = SubElement(channel_elem, "icon")
            icon.set("src", channel["icon_url"])

        return channel_elem

    def serialize_program(self, program: Dict, channel_id: str) -> Element:
        """
        Serialize a program to XMLTV <programme> element.

        Args:
            program: Program dict from database
            channel_id: Channel ID for the programme attribute

        Returns:
            XML Element
        """
        # Required attributes
        prog_elem = Element(
            "programme",
            start=self._format_datetime(program["start_time"]),
            channel=channel_id,
        )

        # Optional stop time
        if program.get("end_time"):
            prog_elem.set("stop", self._format_datetime(program["end_time"]))

        # Title (required)
        title = SubElement(prog_elem, "title")
        title.text = program.get("title", "Unknown Title")
        title.set("lang", "en")

        # Sub-title (episode title)
        if program.get("subtitle"):
            sub_title = SubElement(prog_elem, "sub-title")
            sub_title.text = program["subtitle"]
            sub_title.set("lang", "en")

        # Description
        if program.get("description"):
            desc = SubElement(prog_elem, "desc")
            desc.text = program["description"]
            desc.set("lang", "en")

        # Credits
        credits_elem = None

        # Parse JSON fields
        actors = self._parse_json_field(program.get("actors"))
        directors = self._parse_json_field(program.get("directors"))
        presenters = self._parse_json_field(program.get("presenters"))
        writers = self._parse_json_field(program.get("writers"))
        producers = self._parse_json_field(program.get("producers"))

        if any([actors, directors, presenters, writers, producers]):
            credits_elem = SubElement(prog_elem, "credits")

            # Directors
            if directors:
                for director in directors:
                    if director:
                        director_elem = SubElement(credits_elem, "director")
                        director_elem.text = director

            # Actors (with optional role from categories or custom field)
            if actors:
                for actor in actors:
                    if actor:
                        actor_elem = SubElement(credits_elem, "actor")
                        actor_elem.text = actor

            # Presenters
            if presenters:
                for presenter in presenters:
                    if presenter:
                        presenter_elem = SubElement(credits_elem, "presenter")
                        presenter_elem.text = presenter

            # Writers
            if writers:
                for writer in writers:
                    if writer:
                        writer_elem = SubElement(credits_elem, "writer")
                        writer_elem.text = writer

            # Producers
            if producers:
                for producer in producers:
                    if producer:
                        producer_elem = SubElement(credits_elem, "producer")
                        producer_elem.text = producer

        # Date (production year)
        if program.get("production_year"):
            date_elem = SubElement(prog_elem, "date")
            date_elem.text = program["production_year"]

        # Category
        if program.get("category"):
            category = SubElement(prog_elem, "category")
            category.text = program["category"]
            category.set("lang", "en")

        # Categories from Ultimate Backend (JSON array)
        categories = self._parse_json_field(program.get("categories"))
        if categories:
            for cat in categories:
                if cat:
                    category = SubElement(prog_elem, "category")
                    category.text = str(cat)
                    category.set("lang", "en")

        # Language
        if program.get("language"):
            language = SubElement(prog_elem, "language")
            language.text = program["language"]

        # Original language
        if program.get("orig_language"):
            orig_lang = SubElement(prog_elem, "orig-language")
            orig_lang.text = program["orig_language"]

        # Length (from duration)
        if program.get("start_time") and program.get("end_time"):
            duration = (
                program["end_time"] - program["start_time"]
            ).total_seconds() / 60
            if duration > 0:
                length = SubElement(prog_elem, "length")
                length.text = str(int(duration))
                length.set("units", "minutes")

        # Icon/Thumbnail
        icon_url = program.get("icon_url") or program.get("thumbnail_url")
        if icon_url:
            icon = SubElement(prog_elem, "icon")
            icon.set("src", icon_url)

        # Country
        if program.get("country"):
            country = SubElement(prog_elem, "country")
            country.text = program["country"]

        # Episode numbering: avoid duplicate <episode-num> elements.
        # Strategy:
        #   1. If we have numeric season_num AND episode_num from Ultimate Backend,
        #      emit BOTH onscreen (human-readable) AND xmltv_ns (machine-readable).
        #   2. If we only have a raw episode_num string, emit only onscreen.
        season_num = program.get("season_num")
        raw_episode_num = program.get("episode_num")

        has_structured_episode = season_num is not None and raw_episode_num is not None

        if has_structured_episode:
            # Check if both values are integers (not just strings like "E05")
            try:
                season_int = int(season_num)
                episode_int = int(raw_episode_num)

                # xmltv_ns uses 0-based indexing: (season-1).(episode-1).0/1
                ns_season = max(season_int - 1, 0)
                ns_episode_num = max(episode_int - 1, 0)
                ns_value = f"{ns_season}.{ns_episode_num}.0/1"

                # Emit xmltv_ns (machine-readable, 0-based)
                episode_ns = SubElement(prog_elem, "episode-num")
                episode_ns.text = ns_value
                episode_ns.set("system", "xmltv_ns")

                # Also emit onscreen with human-readable 1-based format
                episode_onscreen = SubElement(prog_elem, "episode-num")
                episode_onscreen.text = f"S{season_int:02d}E{episode_int:02d}"
                episode_onscreen.set("system", "onscreen")

            except (ValueError, TypeError):
                # Values are not integers — fall back to onscreen only
                if raw_episode_num:
                    episode = SubElement(prog_elem, "episode-num")
                    episode.text = str(raw_episode_num)
                    episode.set("system", "onscreen")
        elif raw_episode_num:
            # Only raw episode number available — emit onscreen only
            episode = SubElement(prog_elem, "episode-num")
            episode.text = str(raw_episode_num)
            episode.set("system", "onscreen")

        # New flag (from has_episode_info or epg_flags)
        if program.get("has_episode_info") or program.get("epg_flags"):
            new_elem = SubElement(prog_elem, "new")

        # Rating
        rating_value = program.get("rating")
        if rating_value:
            rating = SubElement(prog_elem, "rating")
            value = SubElement(rating, "value")
            value.text = rating_value

        # Star rating (from Ultimate Backend)
        star_rating = program.get("star_rating")
        if star_rating is not None:
            star_rating_elem = SubElement(prog_elem, "star-rating")
            star_rating_elem.set("system", "IMDB")
            value = SubElement(star_rating_elem, "value")
            value.text = f"{star_rating}/10"

        # Provider-native listing guid (from Ultimate Backend's /epg endpoint
        # only — not populated for grid-imported programs). Not a standard
        # XMLTV element; custom extension so downstream consumers that know
        # to look for it (e.g. nPVR timer scheduling) can pick it up.
        if program.get("epg_event_id"):
            epg_event = SubElement(prog_elem, "epg-event-id")
            epg_event.text = program["epg_event_id"]

        return prog_elem

    def _build_tv_element(
        self,
        channels: List[Dict],
        programs: List[Dict],
        generator_info_name: str,
        generator_info_url: str,
        source_info_name: str,
        source_info_url: str,
    ) -> Element:
        """Build the <tv> Element tree shared by both serialize_tv() and
        serialize_tv_to_file(). Pulled out so the two entry points can't
        drift apart."""
        tv_attrs = {
            "generator-info-name": generator_info_name,
            # datetime.utcnow() is deprecated (3.12+) and returns a naive
            # datetime that merely *happens* to hold UTC values. Using
            # datetime.now(timezone.utc) instead makes the UTC-ness
            # explicit/aware, which is exactly the property _format_datetime
            # now also depends on.
            "date": self._format_datetime(datetime.now(timezone.utc)),
        }

        if generator_info_url:
            tv_attrs["generator-info-url"] = generator_info_url
        if source_info_name:
            tv_attrs["source-info-name"] = source_info_name
        if source_info_url:
            tv_attrs["source-info-url"] = source_info_url

        tv_elem = Element("tv", tv_attrs)

        # Add channels (deduplicate by id)
        seen_channels = set()
        for channel in channels:
            if channel["id"] not in seen_channels:
                seen_channels.add(channel["id"])
                tv_elem.append(self.serialize_channel(channel))

        # Add programs (group by channel)
        for program in programs:
            channel_id = program.get("channel_identifier") or str(
                program.get("channel_id")
            )
            if channel_id:
                tv_elem.append(self.serialize_program(program, channel_id))

        return tv_elem

    def serialize_tv(
        self,
        channels: List[Dict],
        programs: List[Dict],
        generator_info_name: str = "EPG Service",
        generator_info_url: str = "",
        source_info_name: str = "",
        source_info_url: str = "",
    ) -> str:
        """
        Serialize complete TV listing to XMLTV format, pretty-printed.

        Kept for the uncompressed /epg.xml endpoint and anywhere a plain
        string is genuinely needed. For large exports (the .xml.gz path),
        use serialize_tv_to_file() instead -- this method builds the
        document three times over (ElementTree, tostring() bytes, then a
        full minidom DOM just to pretty-print) which is what drove the
        memory spike on magentaeu_at.

        Args:
            channels: List of channel dicts
            programs: List of program dicts with channel_id mapping
            generator_info_name: Name of the generator
            generator_info_url: URL of the generator
            source_info_name: Name of the data source
            source_info_url: URL of the data source

        Returns:
            XMLTV formatted string
        """
        tv_elem = self._build_tv_element(
            channels, programs,
            generator_info_name, generator_info_url,
            source_info_name, source_info_url,
        )

        # Convert to pretty XML string
        rough_string = tostring(tv_elem, "utf-8")
        reparsed = minidom.parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")

        # Add DOCTYPE
        xml_parts = pretty_xml.split("\n", 1)
        if len(xml_parts) > 1:
            result = f"{xml_parts[0]}\n{self.DOCTYPE}\n{xml_parts[1]}"
        else:
            result = pretty_xml

        return result

    def serialize_tv_to_file(
        self,
        channels: List[Dict],
        programs: List[Dict],
        fileobj: BinaryIO,
        generator_info_name: str = "EPG Service",
        generator_info_url: str = "",
        source_info_name: str = "",
        source_info_url: str = "",
    ) -> None:
        """
        Serialize complete TV listing to XMLTV format, writing directly to
        a binary file-like object (e.g. a GzipFile) instead of returning a
        string.

        This skips the tostring() -> minidom.parseString() -> toprettyxml()
        round trip entirely: that chain builds the whole document as bytes,
        then rebuilds it a second time as a minidom DOM (which has much
        higher per-node overhead than ElementTree) purely to add
        indentation, then produces a third full copy as the pretty-printed
        string. For large providers that's 2-3 extra full-size documents
        alive in memory simultaneously on top of the ElementTree itself --
        this was the primary driver of the OOM kill during the
        magentaeu_at export. Output here is not indented; XMLTV consumers
        don't require pretty-printing, only humans reading the raw file do.

        Args:
            channels: List of channel dicts
            programs: List of program dicts with channel_id mapping
            fileobj: Binary file-like object to write the XML into
            generator_info_name: Name of the generator
            generator_info_url: URL of the generator
            source_info_name: Name of the data source
            source_info_url: URL of the data source
        """
        tv_elem = self._build_tv_element(
            channels, programs,
            generator_info_name, generator_info_url,
            source_info_name, source_info_url,
        )

        fileobj.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        fileobj.write(self.DOCTYPE.encode("utf-8") + b"\n")
        ElementTree(tv_elem).write(fileobj, encoding="utf-8", xml_declaration=False)