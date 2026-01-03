"""
Import service for downloading and importing XMLTV data.
"""

import logging
import os
import tempfile
from datetime import datetime
from typing import List, Tuple

import requests

from ..database.connection import get_db
from ..database.models import ImportLog, Provider
from ..parsers.xmltv_parser import XMLTVParser
from .epg_service import EPGService
from .provider_service import ProviderService

logger = logging.getLogger(__name__)


class ImportService:
    """Service for importing XMLTV data from providers."""

    def __init__(self):
        self.parser = XMLTVParser()
        self.provider_service = ProviderService()
        self.epg_service = EPGService()

    def _download_xmltv(self, url: str) -> str:
        """
        Download XMLTV file from URL to temporary file.

        Args:
            url: URL to XMLTV file

        Returns:
            Path to downloaded temporary file

        Raises:
            requests.RequestException: If download fails
        """
        logger.info(f"Downloading XMLTV from {url}")

        try:
            response = requests.get(url, stream=True, timeout=300)
            response.raise_for_status()

            # Create temporary file
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".xml", delete=False
            ) as tmp_file:
                # Stream download to avoid memory issues
                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)

                tmp_path = tmp_file.name

            logger.info(f"Downloaded XMLTV to {tmp_path}")
            return tmp_path

        except requests.RequestException as e:
            logger.error(f"Failed to download XMLTV from {url}: {e}")
            raise

    def _process_channels(self, provider_id: int, file_path: str) -> None:
        """
        Process channels from XMLTV and create channel mappings.

        Args:
            provider_id: Provider ID
            file_path: Path to XMLTV file
        """
        logger.info(f"Processing channels for provider {provider_id}")

        for channel_data in self.parser.parse_channels(file_path):
            try:
                # Get or create logical channel
                channel = self.epg_service.get_or_create_channel(
                    name=channel_data["id"],
                    display_name=channel_data["display_name"],
                    icon_url=channel_data.get("icon_url"),
                )

                # Check if mapping already exists
                existing_channel_id = (
                    self.provider_service.get_channel_for_provider_channel(
                        provider_id=provider_id, provider_channel_id=channel_data["id"]
                    )
                )

                if existing_channel_id is None:
                    # Create mapping
                    self.provider_service.create_channel_mapping(
                        provider_id=provider_id,
                        provider_channel_id=channel_data["id"],
                        channel_id=channel.id,
                    )

            except Exception as e:
                logger.error(f"Error processing channel {channel_data.get('id')}: {e}")
                # Continue with next channel

    def _process_programs(self, provider_id: int, file_path: str) -> Tuple[int, int]:
        """
        Process programs from XMLTV and insert into database.

        Args:
            provider_id: Provider ID
            file_path: Path to XMLTV file

        Returns:
            Tuple of (programs_imported, programs_skipped)
        """
        logger.info(f"Processing programs for provider {provider_id}")

        db = get_db()
        imported = 0
        skipped = 0
        batch = []
        batch_size = 1000

        # Prepare INSERT OR IGNORE statement for duplicate detection
        sql = """
              INSERT \
              OR IGNORE INTO programs (
                channel_id, provider_id, start_time, end_time,
                title, subtitle, description, category, episode_num,
                rating, actors, directors, icon_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) \
              """

        for program_data in self.parser.parse_programs(file_path):
            try:
                # Get logical channel ID from mapping
                channel_id = self.provider_service.get_channel_for_provider_channel(
                    provider_id=provider_id,
                    provider_channel_id=program_data["channel_id"],
                )

                if channel_id is None:
                    skipped += 1
                    continue

                # Prepare program tuple
                program_tuple = (
                    channel_id,
                    provider_id,
                    program_data["start_time"].isoformat(),
                    program_data["end_time"].isoformat(),
                    program_data["title"],
                    program_data.get("subtitle"),
                    program_data.get("description"),
                    program_data.get("category"),
                    program_data.get("episode_num"),
                    program_data.get("rating"),
                    program_data.get("actors"),
                    program_data.get("directors"),
                    program_data.get("icon_url"),
                )

                batch.append(program_tuple)

                # Execute batch insert when batch is full
                if len(batch) >= batch_size:
                    rows_affected = db.executemany(sql, batch)
                    imported += rows_affected
                    skipped += len(batch) - rows_affected
                    batch = []

                    logger.debug(f"Imported batch: {imported} total, {skipped} skipped")

            except Exception as e:
                logger.error(f"Error processing program: {e}")
                skipped += 1

        # Insert remaining batch
        if batch:
            rows_affected = db.executemany(sql, batch)
            imported += rows_affected
            skipped += len(batch) - rows_affected

        logger.info(
            f"Completed program import for provider {provider_id}: "
            f"{imported} imported, {skipped} skipped"
        )

        return imported, skipped

    def import_provider(self, provider_id: int) -> ImportLog:
        """
        Import XMLTV data for a single provider.

        Args:
            provider_id: Provider ID

        Returns:
            ImportLog with import results
        """
        db = get_db()

        # Get provider
        provider = self.provider_service.get_provider(provider_id)
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        if not provider.enabled:
            raise ValueError(f"Provider {provider_id} is disabled")

        logger.info(
            f"Starting import for provider: {provider.name} (ID: {provider_id})"
        )

        # Create import log entry
        log_id = None
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO import_log (provider_id, status)
                    VALUES (?, 'running')
                    """,
                    (provider_id,),
                )
                log_id = cursor.lastrowid
        except Exception as e:
            logger.error(f"Failed to create import log: {e}")
            raise

        tmp_file = None

        try:
            # Download XMLTV file
            tmp_file = self._download_xmltv(provider.xmltv_url)

            # Process channels first
            self._process_channels(provider_id, tmp_file)

            # Process programs
            imported, skipped = self._process_programs(provider_id, tmp_file)

            # Update import log with success
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE import_log
                    SET status            = 'success',
                        completed_at      = CURRENT_TIMESTAMP,
                        programs_imported = ?,
                        programs_skipped  = ?
                    WHERE id = ?
                    """,
                    (imported, skipped, log_id),
                )

            logger.info(f"Successfully imported provider {provider.name}")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Import failed for provider {provider.name}: {error_msg}")

            # Update import log with failure
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE import_log
                    SET status        = 'failed',
                        completed_at  = CURRENT_TIMESTAMP,
                        error_message = ?
                    WHERE id = ?
                    """,
                    (error_msg, log_id),
                )

            raise

        finally:
            # Clean up temporary file
            if tmp_file and os.path.exists(tmp_file):
                try:
                    os.unlink(tmp_file)
                    logger.debug(f"Cleaned up temporary file: {tmp_file}")
                except Exception as e:
                    logger.warning(f"Failed to clean up temporary file: {e}")

        # Fetch and return import log
        row = db.fetchone(
            """
            SELECT id,
                   provider_id,
                   started_at,
                   completed_at,
                   status,
                   programs_imported,
                   programs_skipped,
                   error_message
            FROM import_log
            WHERE id = ?
            """,
            (log_id,),
        )

        return ImportLog.from_db_row(tuple(row))

    def import_all_providers(self) -> List[ImportLog]:
        """
        Import XMLTV data for all enabled providers.

        Returns:
            List of ImportLog objects for each provider
        """
        logger.info("Starting import for all enabled providers")

        providers = self.provider_service.list_providers(enabled_only=True)
        logs = []

        for provider in providers:
            try:
                log = self.import_provider(provider.id)
                logs.append(log)
            except Exception as e:
                logger.error(f"Failed to import provider {provider.name}: {e}")
                # Continue with next provider

        logger.info(f"Completed import for {len(logs)} providers")
        return logs
