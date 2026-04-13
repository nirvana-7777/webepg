"""
XMLTV serializer for exporting EPG data to XMLTV format.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from xml.etree.ElementTree import Element, SubElement, tostring
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
        # Convert to UTC and format
        utc_dt = dt.astimezone()
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
            duration = (program["end_time"] - program["start_time"]).total_seconds() / 60
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

        # Episode number
        episode_num = program.get("episode_num")
        if episode_num:
            episode = SubElement(prog_elem, "episode-num")
            episode.text = episode_num
            episode.set("system", "onscreen")

        # Episode number from Ultimate Backend (season_num, episode_num)
        if program.get("season_num") is not None or program.get("episode_num"):
            season = program.get("season_num", 0)
            episode = program.get("episode_num", 0)
            # xmltv_ns format: season.episode.0/1
            ns_episode = f"{season} . {episode} . 0/1"
            episode_ns = SubElement(prog_elem, "episode-num")
            episode_ns.text = ns_episode
            episode_ns.set("system", "xmltv_ns")

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

        return prog_elem

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
        Serialize complete TV listing to XMLTV format.

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
        # Create root element
        tv_attrs = {
            "generator-info-name": generator_info_name,
            "date": self._format_datetime(datetime.utcnow()),
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
            channel_id = program.get("channel_identifier") or str(program.get("channel_id"))
            if channel_id:
                tv_elem.append(self.serialize_program(program, channel_id))

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