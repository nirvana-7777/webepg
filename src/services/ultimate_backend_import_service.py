"""
Incremental import service for Ultimate Backend EPG data.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

from ..clients.models import UltimateBackendProgram
from ..clients.ultimate_backend_client import UltimateBackendClient
from ..database.connection import get_db

logger = logging.getLogger(__name__)


class UltimateBackendImportService:
    """Incremental EPG import from Ultimate Backend."""

    def __init__(
        self,
        client: UltimateBackendClient,
        future_days: int = 7,
        past_days: int = 7,
        chunk_hours: int = 24,
        max_concurrent_channels: int = 3,
    ):
        self.client = client
        self.future_days = future_days
        self.past_days = past_days
        self.chunk_hours = chunk_hours
        self.max_concurrent_channels = max_concurrent_channels
        self._semaphore = asyncio.Semaphore(max_concurrent_channels)

    async def incremental_import_all(self) -> Dict:
        """
        Import all channels incrementally.

        Returns:
            Dict with import statistics
        """
        logger.info("Starting incremental import for all Ultimate Backend channels")

        db = get_db()

        # Get all active ultimate channels with mappings
        rows = db.fetchall("""
            SELECT 
                uc.id as ultimate_channel_id,
                uc.ultimate_channel_id as channel_id,
                uc.channel_name,
                up.provider_name,
                ucm.channel_id as logical_channel_id
            FROM ultimate_channels uc
            JOIN ultimate_providers up ON uc.ultimate_provider_id = up.id
            JOIN ultimate_channel_mappings ucm ON uc.id = ucm.ultimate_channel_id
            WHERE uc.enabled = 1 AND up.enabled = 1
        """)

        if not rows:
            logger.info("No ultimate channels found to import")
            return {"total_channels": 0, "results": []}

        logger.info(f"Found {len(rows)} channels to import")

        # Process channels concurrently with semaphore
        tasks = []
        for row in rows:
            task = self._import_channel_with_semaphore(
                ultimate_channel_db_id=row["ultimate_channel_id"],
                provider_name=row["provider_name"],
                ultimate_channel_id=row["channel_id"],
                logical_channel_id=row["logical_channel_id"],
                channel_name=row["channel_name"],
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Compile statistics
        stats = {
            "total_channels": len(rows),
            "successful": 0,
            "failed": 0,
            "total_programs_inserted": 0,
            "total_programs_updated": 0,
            "total_programs_skipped": 0,
            "results": [],
        }

        for result in results:
            if isinstance(result, Exception):
                stats["failed"] += 1
                stats["results"].append({"error": str(result)})
            else:
                stats["successful"] += 1
                stats["total_programs_inserted"] += result.get("inserted", 0)
                stats["total_programs_updated"] += result.get("updated", 0)
                stats["total_programs_skipped"] += result.get("skipped", 0)
                stats["results"].append(result)

        logger.info(
            f"Import complete: {stats['successful']}/{stats['total_channels']} channels, "
            f"inserted={stats['total_programs_inserted']}, "
            f"updated={stats['total_programs_updated']}, "
            f"skipped={stats['total_programs_skipped']}"
        )

        return stats

    async def _import_channel_with_semaphore(self, **kwargs) -> Dict:
        """Import a channel with semaphore limiting."""
        async with self._semaphore:
            return await self.incremental_import_channel(**kwargs)

    async def incremental_import_channel(
        self,
        ultimate_channel_db_id: int,
        provider_name: str,
        ultimate_channel_id: str,
        logical_channel_id: int,
        channel_name: str,
    ) -> Dict:
        """
        Incrementally import a single channel.

        Returns:
            Dict with import statistics for this channel
        """
        logger.info(
            f"Importing channel: {channel_name} ({provider_name}/{ultimate_channel_id})"
        )

        stats = {
            "channel_id": ultimate_channel_db_id,
            "channel_name": channel_name,
            "provider": provider_name,
            "chunks": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "errors": [],
        }

        db = get_db()

        # Get current import state
        state = db.fetchone(
            "SELECT last_imported_until FROM channel_import_state WHERE ultimate_channel_id = ?",
            (ultimate_channel_db_id,),
        )

        # Calculate time range
        now = datetime.utcnow()
        start_time = (
            self._parse_last_imported(state)
            if state
            else now - timedelta(days=self.past_days)
        )
        end_target = now + timedelta(days=self.future_days)

        # Update state to in_progress
        db.execute(
            "UPDATE channel_import_state SET sync_status = 'in_progress', updated_at = ? WHERE ultimate_channel_id = ?",
            (datetime.utcnow().isoformat(), ultimate_channel_db_id),
        )

        current = start_time

        try:
            while current < end_target:
                chunk_end = min(current + timedelta(hours=self.chunk_hours), end_target)

                logger.debug(
                    f"Fetching chunk: {current.isoformat()} to {chunk_end.isoformat()}"
                )

                # Fetch programs for this chunk
                chunk_stats = await self._import_chunk(
                    provider_name=provider_name,
                    ultimate_channel_id=ultimate_channel_id,
                    logical_channel_id=logical_channel_id,
                    start_time=current,
                    end_time=chunk_end,
                )

                stats["chunks"] += 1
                stats["inserted"] += chunk_stats["inserted"]
                stats["updated"] += chunk_stats["updated"]
                stats["skipped"] += chunk_stats["skipped"]

                if chunk_stats.get("error"):
                    stats["errors"].append(chunk_stats["error"])

                # Update last_imported_until
                db.execute(
                    """
                    UPDATE channel_import_state
                    SET last_imported_until = ?, updated_at = ?, program_count = program_count + ?
                    WHERE ultimate_channel_id = ?
                    """,
                    (
                        chunk_end.isoformat(),
                        datetime.utcnow().isoformat(),
                        chunk_stats["inserted"],
                        ultimate_channel_db_id,
                    ),
                )

                current = chunk_end

                # Small delay between chunks
                await asyncio.sleep(0.5)

            # Mark as success
            db.execute(
                """
                UPDATE channel_import_state
                SET sync_status = 'success', last_successful_sync = ?, updated_at = ?
                WHERE ultimate_channel_id = ?
                """,
                (
                    datetime.utcnow().isoformat(),
                    datetime.utcnow().isoformat(),
                    ultimate_channel_db_id,
                ),
            )

            logger.info(
                f"Channel {channel_name}: {stats['inserted']} inserted, {stats['updated']} updated, {stats['skipped']} skipped"
            )

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Import failed for {channel_name}: {error_msg}")
            stats["errors"].append(error_msg)

            db.execute(
                """
                UPDATE channel_import_state
                SET sync_status = 'failed', last_error = ?, updated_at = ?
                WHERE ultimate_channel_id = ?
                """,
                (
                    error_msg[:500],
                    datetime.utcnow().isoformat(),
                    ultimate_channel_db_id,
                ),
            )

            raise

        return stats

    async def _import_chunk(
        self,
        provider_name: str,
        ultimate_channel_id: str,
        logical_channel_id: int,
        start_time: datetime,
        end_time: datetime,
    ) -> Dict:
        """
        Import a single time chunk.

        Returns:
            Dict with chunk statistics
        """
        db = get_db()
        batch_id = None

        # Create batch record
        try:
            db.execute(
                """
                INSERT INTO import_batches (ultimate_channel_id, batch_start, batch_end, status)
                VALUES ((SELECT id FROM ultimate_channels WHERE ultimate_channel_id = ? AND ultimate_provider_id IN 
                         (SELECT id FROM ultimate_providers WHERE provider_name = ?)), ?, ?, 'pending')
                """,
                (
                    ultimate_channel_id,
                    provider_name,
                    start_time.isoformat(),
                    end_time.isoformat(),
                ),
            )
            row = db.fetchone("SELECT last_insert_rowid()")
            batch_id = row[0] if row else None
        except Exception as e:
            logger.warning(f"Failed to create batch record: {e}")

        start_ms = time.time()

        try:
            # Fetch programs from API
            programs_data = await self.client.get_epg(
                provider_name=provider_name,
                channel_id=ultimate_channel_id,
                start_time=start_time,
                end_time=end_time,
            )

            fetch_duration = time.time() - start_ms

            if not programs_data:
                logger.debug(f"No programs found for chunk {start_time} to {end_time}")
                self._update_batch(
                    batch_id, 0, 0, 0, 0, fetch_duration * 1000, "success"
                )
                return {"inserted": 0, "updated": 0, "skipped": 0}

            # Process each program
            inserted = 0
            updated = 0
            skipped = 0

            for prog_data in programs_data:
                try:
                    program = UltimateBackendProgram.from_api_response(prog_data)
                    result = self._upsert_program(
                        program, logical_channel_id, provider_name
                    )

                    if result == "inserted":
                        inserted += 1
                    elif result == "updated":
                        updated += 1
                    else:
                        skipped += 1

                except Exception as e:
                    logger.warning(f"Failed to process program: {e}")
                    skipped += 1

            total_duration = time.time() - start_ms

            # Update batch record
            self._update_batch(
                batch_id,
                len(programs_data),
                inserted,
                updated,
                skipped,
                total_duration * 1000,
                "success",
            )

            return {"inserted": inserted, "updated": updated, "skipped": skipped}

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Chunk import failed: {error_msg}")
            self._update_batch(batch_id, 0, 0, 0, 0, 0, "failed", error_msg)
            return {"inserted": 0, "updated": 0, "skipped": 0, "error": error_msg}

    @staticmethod
    def _upsert_program(
        program: UltimateBackendProgram,
        logical_channel_id: int,
        provider_name: str,
    ) -> str:
        """
        Insert or update a program based on ultimate_epg_id.

        Returns:
            'inserted', 'updated', or 'skipped'
        """
        db = get_db()

        # Check if program exists
        existing = db.fetchone(
            "SELECT id, title, start_time, end_time, description FROM programs WHERE ultimate_epg_id = ?",
            (program.epg_id,),
        )

        program_dict = program.to_dict()
        program_dict["channel_id"] = logical_channel_id

        # Get provider_id from provider_name
        provider_row = db.fetchone(
            "SELECT id FROM providers WHERE name = ?", (provider_name,)
        )
        if not provider_row:
            # Create a provider entry for Ultimate Backend if not exists
            db.execute(
                "INSERT INTO providers (name, display_name, source_type) VALUES (?, ?, 'ultimate_backend')",
                (provider_name, provider_name),
            )
            provider_row = db.fetchone("SELECT last_insert_rowid()")

        program_dict["provider_id"] = provider_row[0]

        if existing:
            # Check if program has changed (compare relevant fields)
            existing_id = existing[0]
            existing_title = existing[1]
            existing_start = existing[2]
            existing_end = existing[3]
            existing_desc = existing[4]

            # Simple change detection
            changed = existing_title != program.title or existing_desc != program.plot

            if changed:
                # Update existing
                db.execute(
                    """
                    UPDATE programs
                    SET title = ?, subtitle = ?, description = ?, start_time = ?, end_time = ?,
                        category = ?, season_num = ?, episode_num = ?, director = ?, actors = ?,
                        producer = ?, production_year = ?, rating = ?, thumbnail_url = ?,
                        images = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        program.title,
                        program.episode_title,
                        program.plot,
                        program.start.isoformat(),
                        program.end.isoformat(),
                        program.genre,
                        program.season_num,
                        program.episode_num,
                        program.director,
                        json.dumps(program.cast) if program.cast else None,
                        program.producer,
                        str(program.year) if program.year else None,
                        str(program.rating) if program.rating else None,
                        program.thumbnail,
                        json.dumps(program.images) if program.images else None,
                        existing_id,
                    ),
                )
                logger.debug(f"Updated program {program.epg_id}: {program.title}")
                return "updated"
            else:
                # No change, skip
                return "skipped"
        else:
            # Insert new
            db.execute(
                """
                INSERT INTO programs (
                    channel_id, provider_id, start_time, end_time, title, subtitle, description,
                    category, ultimate_epg_id, schedule_id, season_num, episode_num,
                    has_episode_info, director, actors, producer, production_year,
                    rating, thumbnail_url, images, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    logical_channel_id,
                    program_dict["provider_id"],
                    program_dict["start_time"],
                    program_dict["end_time"],
                    program_dict["title"],
                    program_dict.get("subtitle"),
                    program_dict.get("description"),
                    program_dict.get("category"),
                    program.epg_id,
                    program.schedule_id,
                    program.season_num,
                    program.episode_num,
                    program.has_episode_info,
                    program.director,
                    json.dumps(program.cast) if program.cast else None,
                    program.producer,
                    program_dict.get("production_year"),
                    program_dict.get("rating"),
                    program.thumbnail,
                    json.dumps(program.images) if program.images else None,
                ),
            )
            logger.debug(f"Inserted program {program.epg_id}: {program.title}")
            return "inserted"

    @staticmethod
    def _update_batch(
        batch_id: Optional[int],
        fetched: int,
        inserted: int,
        updated: int,
        skipped: int,
        duration_ms: float,
        status: str,
        error_msg: Optional[str] = None,
    ):
        """Update batch record with results."""
        if not batch_id:
            return

        db = get_db()
        db.execute(
            """
            UPDATE import_batches
            SET programs_fetched = ?, programs_inserted = ?, programs_updated = ?,
                programs_skipped = ?, duration_ms = ?, status = ?, error_message = ?
            WHERE id = ?
            """,
            (
                fetched,
                inserted,
                updated,
                skipped,
                int(duration_ms),
                status,
                error_msg,
                batch_id,
            ),
        )

    @staticmethod
    def _parse_last_imported(state) -> datetime:
        """Parse last_imported_until from database row."""
        if not state or not state[0]:
            return datetime.utcnow() - timedelta(days=7)

        try:
            return datetime.fromisoformat(state[0])
        except (ValueError, TypeError):
            return datetime.utcnow() - timedelta(days=7)
