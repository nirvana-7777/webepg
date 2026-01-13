"""
Streaming XMLTV parser for efficient memory usage.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, Iterator, Optional

logger = logging.getLogger(__name__)


class XMLTVParser:
    """Memory-efficient streaming parser for XMLTV files."""

    @staticmethod
    def _parse_datetime(dt_str: str) -> Optional[datetime]:
        """
        Parse XMLTV datetime format (YYYYMMDDHHmmss +ZZZZ).

        Args:
            dt_str: Datetime string from XMLTV

        Returns:
            datetime object in UTC or None if parsing fails
        """
        if not dt_str:
            return None

        try:
            # XMLTV format: 20250101120000 +0100
            # Split datetime and timezone
            parts = dt_str.split()
            dt_part = parts[0]

            # Parse base datetime as UTC by default
            dt = datetime.strptime(dt_part[:14], "%Y%m%d%H%M%S")

            # Handle timezone offset if present
            if len(parts) > 1:
                tz_str = parts[1]
                # Convert +0100 to hours offset
                sign = 1 if tz_str[0] == "+" else -1
                hours = int(tz_str[1:3])
                minutes = int(tz_str[3:5])

                # Adjust datetime (convert to UTC)
                from datetime import timedelta

                offset = timedelta(hours=sign * hours, minutes=sign * minutes)
                dt = dt - offset

            # Ensure we return aware datetime in UTC
            from datetime import timezone

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            return dt
        except Exception as e:
            logger.warning(f"Failed to parse datetime '{dt_str}': {e}")
            return None

    @staticmethod
    def _get_text(element: ET.Element, tag: str, lang: str = "en") -> Optional[str]:
        """
        Get text content from child element, preferring specified language.

        Args:
            element: Parent XML element
            tag: Child tag name
            lang: Preferred language code

        Returns:
            Text content or None
        """
        # Try to find element with preferred language
        for child in element.findall(tag):
            if child.get("lang", "en") == lang:
                return child.text

        # Fallback to first element
        child = element.find(tag)
        return child.text if child is not None else None

    @staticmethod
    def _get_credits(element: ET.Element) -> Dict[str, list]:
        """
        Extract credits (actors, directors, etc.) from programme element.

        Args:
            element: Programme XML element

        Returns:
            Dictionary with 'actors' and 'directors' as lists
        """
        credits = {
            "actors": [],
            "directors": [],
            "presenters": [],
            "writers": [],
            "producers": [],
        }

        credits_elem = element.find("credits")
        if credits_elem is not None:
            # Get all actors
            actors = [
                actor.text for actor in credits_elem.findall("actor") if actor.text
            ]
            if actors:
                credits["actors"] = actors

            # Get all directors
            directors = [
                director.text
                for director in credits_elem.findall("director")
                if director.text
            ]
            if directors:
                credits["directors"] = directors

            # Get presenters
            presenters = [
                presenter.text
                for presenter in credits_elem.findall("presenter")
                if presenter.text
            ]
            if presenters:
                credits["presenters"] = presenters

            # Get writers
            writers = [
                writer.text for writer in credits_elem.findall("writer") if writer.text
            ]
            if writers:
                credits["writers"] = writers

            # Get producers
            producers = [
                producer.text
                for producer in credits_elem.findall("producer")
                if producer.text
            ]
            if producers:
                credits["producers"] = producers

        return credits

    def parse_channels(self, file_path: str) -> Iterator[Dict[str, str]]:
        """
        Parse channel information from XMLTV file.

        Args:
            file_path: Path to XMLTV file

        Yields:
            Dictionary with channel data:
                - id: Channel ID
                - display_name: Channel display name
                - icon_url: Channel icon URL (optional)
        """
        try:
            # Use iterparse for memory efficiency
            for event, elem in ET.iterparse(file_path, events=("end",)):
                if elem.tag == "channel":
                    channel_id = elem.get("id")
                    if not channel_id:
                        elem.clear()
                        continue

                    display_name = self._get_text(elem, "display-name")
                    icon_elem = elem.find("icon")
                    icon_url = icon_elem.get("src") if icon_elem is not None else None

                    yield {
                        "id": channel_id,
                        "display_name": display_name or channel_id,
                        "icon_url": icon_url,
                    }

                    # Clear element to free memory
                    elem.clear()
        except ET.ParseError as e:
            logger.error(f"XML parse error in {file_path}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error parsing channels from {file_path}: {e}")
            raise

    def parse_programs(self, file_path: str) -> Iterator[Dict]:
        """
        Parse programme information from XMLTV file.

        Args:
            file_path: Path to XMLTV file

        Yields:
            Dictionary with programme data including date, country, and full credits
        """
        try:
            for event, elem in ET.iterparse(file_path, events=("end",)):
                if elem.tag == "programme":
                    channel_id = elem.get("channel")
                    start_str = elem.get("start")
                    stop_str = elem.get("stop")

                    if not all([channel_id, start_str, stop_str]):
                        elem.clear()
                        continue

                    start_time = self._parse_datetime(start_str)
                    end_time = self._parse_datetime(stop_str)

                    if not start_time or not end_time:
                        elem.clear()
                        continue

                    title = self._get_text(elem, "title")
                    if not title:
                        elem.clear()
                        continue

                    # Extract credits as lists
                    credits = self._get_credits(elem)

                    # Get category
                    category_elem = elem.find("category")
                    category = category_elem.text if category_elem is not None else None

                    # Get episode number
                    episode_elem = elem.find("episode-num")
                    episode_num = (
                        episode_elem.text if episode_elem is not None else None
                    )

                    # Get rating
                    rating_elem = elem.find("rating")
                    rating = None
                    if rating_elem is not None:
                        value_elem = rating_elem.find("value")
                        rating = value_elem.text if value_elem is not None else None

                    # Get icon
                    icon_elem = elem.find("icon")
                    icon_url = icon_elem.get("src") if icon_elem is not None else None

                    # Get date (production year)
                    date_elem = elem.find("date")
                    production_year = date_elem.text if date_elem is not None else None

                    # Get country
                    country_elem = elem.find("country")
                    country = country_elem.text if country_elem is not None else None

                    yield {
                        "channel_id": channel_id,
                        "start_time": start_time,
                        "end_time": end_time,
                        "title": title,
                        "subtitle": self._get_text(elem, "sub-title"),
                        "description": self._get_text(elem, "desc"),
                        "category": category,
                        "episode_num": episode_num,
                        "rating": rating,
                        "actors": credits["actors"],  # Now a list
                        "directors": credits["directors"],  # Now a list
                        "presenters": credits.get("presenters", []),  # New field
                        "writers": credits.get("writers", []),  # New field
                        "producers": credits.get("producers", []),  # New field
                        "icon_url": icon_url,
                        "production_year": production_year,  # New field
                        "country": country,  # New field
                    }

                    # Clear element to free memory
                    elem.clear()
        except ET.ParseError as e:
            logger.error(f"XML parse error in {file_path}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error parsing programs from {file_path}: {e}")
            raise
