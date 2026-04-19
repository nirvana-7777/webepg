# src/services/ultimate_backend_import_service.py

"""
Import service for Ultimate Backend EPG data.

Two import modes:
  - full_import_all()        Bootstrap / manual sync. Ignores existing state,
                             fetches past_days of history and up to
                             api_max_future_days of future data (the API hard
                             cap; currently 3 days).
  - incremental_import_all() Daily job. Picks up from last_imported_until and
                             advances to now + api_max_future_days, skipping
                             any chunks already covered.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from ..clients.models import UltimateBackendProgram
from ..clients.ultimate_backend_client import UltimateBackendClient
from ..database.connection import get_db

logger = logging.getLogger(__name__)


class UltimateBackendImportService:
    """Full and incremental EPG import from Ultimate Backend."""

    def __init__(
        self,
        client: UltimateBackendClient,
        future_days: int = 3,
        past_days: int = 7,
        chunk_hours: int = 24,
        max_concurrent_channels: int = 3,
        api_max_future_days: int = 3,
    ):
        self.client = client
        self.future_days = future_days
        self.past_days = past_days
        self.chunk_hours = chunk_hours
        self.max_concurrent_channels = max_concurrent_channels
        # Hard cap imposed by the Ultimate Backend API.  Requesting data beyond
        # this window returns empty results, so we never ask for more.
        self.api_max_future_days = api_max_future_days

        # NOTE: asyncio.Semaphore must be created inside the running event loop.
        # We reset it to None at the start of every public entry point so it is
        # always bound to the loop that is actually running.
        self._semaphore: Optional[asyncio.Semaphore] = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Return (or lazily create) the semaphore for the current event loop."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent_channels)
        return self._semaphore

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def full_import_all(self) -> Dict:
        """
        Full bootstrap import for all channels.

        Resets per-channel state so that incremental_import_channel will
        fetch everything from scratch: past_days of history and up to
        api_max_future_days of future data.

        Intended for initial setup or a manual "re-sync everything" trigger.
        The HTTP client session is closed before returning.
        """
        logger.info("Starting FULL import for all Ultimate Backend channels")

        # Fresh semaphore for this event loop.
        self._semaphore = None

        db = get_db()

        # Wipe per-channel import state so every channel is treated as new.
        db.execute("""
            UPDATE channel_import_state
            SET last_imported_until = NULL,
                sync_status         = 'pending',
                program_count       = 0
        """)

        rows = self._fetch_active_channel_rows(db)
        if not rows:
            logger.info("No ultimate channels found to import")
            return {"total_channels": 0, "results": []}

        logger.info(f"Found {len(rows)} channels for full import")

        try:
            tasks = [
                self._import_channel_with_semaphore(
                    ultimate_channel_db_id=row["ultimate_channel_id"],
                    provider_name=row["provider_name"],
                    ultimate_channel_id=row["channel_id"],
                    logical_channel_id=row["logical_channel_id"],
                    channel_name=row["channel_name"],
                    force_full=True,
                    provider_future_days=row.get("future_days"),
                    provider_past_days=row.get("past_days"),
                    provider_chunk_hours=row.get("chunk_hours"),
                )
                for row in rows
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await self.client.close()

        return self._compile_stats(rows, results, "Full import")

    async def incremental_import_all(self) -> Dict:
        """
        Incremental import for all channels.

        Each channel advances from its last_imported_until cursor to
        now + api_max_future_days, skipping chunks that are already covered.
        Channels with no state fall back to a full fetch (past_days history).

        The HTTP client session is closed before returning.
        """
        logger.info("Starting incremental import for all Ultimate Backend channels")

        # Fresh semaphore for this event loop.
        self._semaphore = None

        db = get_db()

        rows = self._fetch_active_channel_rows(db)
        if not rows:
            logger.info("No ultimate channels found to import")
            return {"total_channels": 0, "results": []}

        logger.info(f"Found {len(rows)} channels for incremental import")

        try:
            tasks = [
                self._import_channel_with_semaphore(
                    ultimate_channel_db_id=row["ultimate_channel_id"],
                    provider_name=row["provider_name"],
                    ultimate_channel_id=row["channel_id"],
                    logical_channel_id=row["logical_channel_id"],
                    channel_name=row["channel_name"],
                    provider_future_days=row.get("future_days"),
                    provider_past_days=row.get("past_days"),
                    provider_chunk_hours=row.get("chunk_hours"),
                )
                for row in rows
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await self.client.close()

        return self._compile_stats(rows, results, "Incremental import")

    # ------------------------------------------------------------------
    # Per-channel import
    # ------------------------------------------------------------------

    async def _import_channel_with_semaphore(self, **kwargs) -> Dict:
        """Throttle channel imports via the shared semaphore."""
        async with self._get_semaphore():
            return await self.incremental_import_channel(**kwargs)

    async def incremental_import_channel(
        self,
        ultimate_channel_db_id: int,
        provider_name: str,
        ultimate_channel_id: str,
        logical_channel_id: int,
        channel_name: str,
        force_full: bool = False,
        # Configuration overrides from database
        provider_future_days: Optional[int] = None,
        provider_past_days: Optional[int] = None,
        provider_chunk_hours: Optional[int] = None,
    ) -> Dict:
        """
        Import a single channel, either fully or incrementally.

        When force_full=True (or no prior state exists) the fetch window is:
            [now - past_days  …  now + api_max_future_days]

        On a normal incremental run the window is:
            [last_imported_until  …  now + api_max_future_days]
        and any chunk whose end falls at or before last_imported_until is
        skipped immediately without an API call.

        future_days config is clamped to api_max_future_days so we never
        request data the API cannot supply.
        """
        logger.info(
            f"{'Full' if force_full else 'Incremental'} import: "
            f"{channel_name} ({provider_name}/{ultimate_channel_id})"
        )

        # Use provider-specific settings if provided, else fall back to service defaults
        effective_past_days = provider_past_days if provider_past_days is not None else self.past_days
        effective_future_days = provider_future_days if provider_future_days is not None else self.future_days
        effective_chunk_hours = provider_chunk_hours if provider_chunk_hours is not None else self.chunk_hours

        # Clamp future window to the API hard cap (currently 3 days).
        # NOTE: We keep self.api_max_future_days as a safety cap unless 
        # the provider explicitly knows better.
        clamped_future_days = min(effective_future_days, self.api_max_future_days)

        stats: Dict = {
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

        # Fetch current cursor once before the loop.
        state = db.fetchone(
            "SELECT last_imported_until FROM channel_import_state WHERE ultimate_channel_id = ?",
            (ultimate_channel_db_id,),
        )

        now = datetime.now(timezone.utc)

        # Determine start of the fetch window.
        if force_full or not state or not state[0]:
            start_time = now - timedelta(days=effective_past_days)
            last_imported: Optional[datetime] = None
            logger.info(f"  Window start: {start_time.isoformat()} (full, days={effective_past_days})")
        else:
            last_imported = self._parse_datetime_safe(state[0])
            start_time = last_imported
            logger.info(f"  Window start: {start_time.isoformat()} (incremental)")

        end_target = now + timedelta(days=clamped_future_days)

        # Mark in-progress.
        db.execute(
            """
            UPDATE channel_import_state
            SET sync_status = 'in_progress', updated_at = ?
            WHERE ultimate_channel_id = ?
            """,
            (now.isoformat(), ultimate_channel_db_id),
        )

        current = start_time

        try:
            while current < end_target:
                chunk_end = min(current + timedelta(hours=effective_chunk_hours), end_target)

                # Skip chunks already covered by a previous run.
                if last_imported is not None and chunk_end <= last_imported:
                    logger.debug(f"  Skipping covered chunk → {chunk_end.isoformat()}")
                    current = chunk_end
                    continue

                logger.debug(
                    f"  Fetching chunk: {current.isoformat()} → {chunk_end.isoformat()}"
                )

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
                # Count both inserted and updated towards program_count so the
                # counter doesn't drift lower than reality over time.
                stats["skipped"] += chunk_stats["skipped"]

                if chunk_stats.get("error"):
                    stats["errors"].append(chunk_stats["error"])

                # Advance the cursor.  Use Python-level MAX so we never regress
                # if chunks arrive slightly out of order, and so the UPDATE
                # works correctly even when last_imported_until is currently NULL
                # (SQLite's MAX(NULL, x) returns NULL, which would lose the value).
                new_cursor = chunk_end
                if last_imported is not None:
                    new_cursor = max(new_cursor, last_imported)

                db.execute(
                    """
                    UPDATE channel_import_state
                    SET last_imported_until = ?,
                        updated_at          = ?,
                        program_count       = program_count + ?
                    WHERE ultimate_channel_id = ?
                    """,
                    (
                        new_cursor.isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                        chunk_stats["inserted"] + chunk_stats["updated"],
                        ultimate_channel_db_id,
                    ),
                )

                # Keep last_imported in sync so the skip logic stays correct
                # for subsequent chunks in this same run.
                last_imported = new_cursor
                current = chunk_end

                await asyncio.sleep(0.5)

            # Mark success.
            db.execute(
                """
                UPDATE channel_import_state
                SET sync_status        = 'success',
                    last_successful_sync = ?,
                    updated_at           = ?
                WHERE ultimate_channel_id = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    ultimate_channel_db_id,
                ),
            )

            logger.info(
                f"  {channel_name}: inserted={stats['inserted']}, "
                f"updated={stats['updated']}, skipped={stats['skipped']}"
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
                    datetime.now(timezone.utc).isoformat(),
                    ultimate_channel_db_id,
                ),
            )

            raise

        return stats

    # ------------------------------------------------------------------
    # Chunk-level import
    # ------------------------------------------------------------------

    async def _import_chunk(
        self,
        provider_name: str,
        ultimate_channel_id: str,
        logical_channel_id: int,
        start_time: datetime,
        end_time: datetime,
    ) -> Dict:
        """
        Fetch one time-window from the API and upsert all programs.

        Returns a dict with keys: inserted, updated, skipped, error (optional).
        """
        db = get_db()
        batch_id: Optional[int] = None

        try:
            db.execute(
                """
                INSERT INTO import_batches (ultimate_channel_id, batch_start, batch_end, status)
                VALUES (
                    (SELECT id FROM ultimate_channels
                     WHERE ultimate_channel_id = ?
                       AND ultimate_provider_id IN (
                           SELECT id FROM ultimate_providers WHERE provider_name = ?
                       )),
                    ?, ?, 'pending'
                )
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
            programs_data = await self.client.get_epg(
                provider_name=provider_name,
                channel_id=ultimate_channel_id,
                start_time=start_time,
                end_time=end_time,
            )

            fetch_duration = time.time() - start_ms

            if not programs_data:
                logger.debug(f"No programs in chunk {start_time} → {end_time}")
                self._update_batch(batch_id, 0, 0, 0, 0, fetch_duration * 1000, "success")
                return {"inserted": 0, "updated": 0, "skipped": 0}

            inserted = updated = skipped = 0

            for prog_data in programs_data:
                try:
                    program = UltimateBackendProgram.from_api_response(prog_data)
                    result = self._upsert_program(program, logical_channel_id, provider_name)
                    if result == "inserted":
                        inserted += 1
                    elif result == "updated":
                        updated += 1
                    else:
                        skipped += 1
                except Exception as e:
                    logger.warning(
                        f"Failed to process program {prog_data.get('epg_id', '?')}: {e}"
                    )
                    skipped += 1

            total_duration = time.time() - start_ms
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

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------

    @staticmethod
    def _upsert_program(
            program: UltimateBackendProgram,
            logical_channel_id: int,
            provider_name: str,
    ) -> str:
        """
        Insert or update a program using INSERT OR REPLACE or explicit conflict handling.

        Uses SQLite's INSERT OR REPLACE to handle the UNIQUE constraint
        on (channel_id, start_time, end_time).

        Returns 'inserted', 'updated', or 'skipped'.
        """
        db = get_db()

        # Get or create provider using unified schema
        provider_row = db.fetchone(
            "SELECT id FROM providers WHERE name = ? AND source_type = 'ultimate_backend'",
            (provider_name,),
        )

        if not provider_row:
            # Create provider with NULL xmltv_url (allowed in v4)
            db.execute(
                """
                INSERT INTO providers (name, display_name, source_type, has_epg)
                VALUES (?, ?, 'ultimate_backend', 1)
                """,
                (provider_name, provider_name.replace('_', ' ').title()),
            )
            provider_row = db.fetchone("SELECT last_insert_rowid()")

        provider_id = provider_row[0]

        new_start = program.start.isoformat()
        new_end = program.end.isoformat()

        # First, check if program already exists by time slot
        existing = db.fetchone(
            """
            SELECT id, ultimate_epg_id, title, description 
            FROM programs 
            WHERE channel_id = ? AND start_time = ? AND end_time = ?
            """,
            (logical_channel_id, new_start, new_end),
        )

        if existing:
            existing_id = existing[0]
            existing_epg_id = existing[1]

            # Check if this is truly the same program or a different one at same time
            # If it has a different ultimate_epg_id, it's a different program at same time slot
            if existing_epg_id and existing_epg_id != program.epg_id:
                # Different program at same time - this shouldn't happen but if it does,
                # we need to decide which one to keep. Keep the one with more data.
                logger.warning(
                    f"Time slot conflict: existing program {existing_epg_id} vs "
                    f"new {program.epg_id} at {new_start}. Updating with new data."
                )

            # Update existing program
            db.execute(
                """
                UPDATE programs
                SET title            = COALESCE(?, title),
                    subtitle         = COALESCE(?, subtitle),
                    description      = COALESCE(?, description),
                    category         = COALESCE(?, category),
                    ultimate_epg_id  = COALESCE(?, ultimate_epg_id),
                    schedule_id      = COALESCE(?, schedule_id),
                    season_num       = COALESCE(?, season_num),
                    episode_num      = COALESCE(?, episode_num),
                    has_episode_info = COALESCE(?, has_episode_info),
                    director         = COALESCE(?, director),
                    actors           = COALESCE(?, actors),
                    producer         = COALESCE(?, producer),
                    production_year  = COALESCE(?, production_year),
                    rating           = COALESCE(?, rating),
                    thumbnail_url    = COALESCE(?, thumbnail_url),
                    images           = COALESCE(?, images)
                WHERE id = ?
                """,
                (
                    program.title if program.title else None,
                    program.episode_title,
                    program.plot,
                    program.genre,
                    program.epg_id,
                    program.schedule_id,
                    program.season_num,
                    program.episode_num,
                    1 if program.has_episode_info else 0,
                    program.director,
                    json.dumps(program.cast) if program.cast else None,
                    program.producer,
                    str(program.year) if program.year else None,
                    str(program.rating) if program.rating is not None else None,
                    program.thumbnail,
                    json.dumps(program.images) if program.images else None,
                    existing_id,
                ),
            )
            logger.debug(f"Updated program at {new_start}: {program.title}")
            return "updated"

        # No existing program at this time slot - insert new one
        try:
            # Use INSERT OR IGNORE to handle any race conditions
            db.execute(
                """
                INSERT OR IGNORE INTO programs (
                    channel_id, provider_id, start_time, end_time,
                    title, subtitle, description,
                    category, ultimate_epg_id, schedule_id,
                    season_num, episode_num, has_episode_info,
                    director, actors, producer,
                    production_year, rating, thumbnail_url, images
                ) VALUES (
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    logical_channel_id,
                    provider_id,
                    new_start,
                    new_end,
                    program.title,
                    program.episode_title,
                    program.plot,
                    program.genre,
                    program.epg_id,
                    program.schedule_id,
                    program.season_num,
                    program.episode_num,
                    1 if program.has_episode_info else 0,
                    program.director,
                    json.dumps(program.cast) if program.cast else None,
                    program.producer,
                    str(program.year) if program.year else None,
                    str(program.rating) if program.rating is not None else None,
                    program.thumbnail,
                    json.dumps(program.images) if program.images else None,
                ),
            )

            # Check if insert succeeded
            changes = db.fetchone("SELECT changes()")
            if changes and changes[0] > 0:
                logger.debug(f"Inserted program {program.epg_id}: {program.title}")
                return "inserted"
            else:
                # Insert was ignored due to conflict - this means another process
                # inserted the same program. Try to update it instead.
                logger.debug(f"Program at {new_start} was inserted by another process, updating instead")

                # Get the existing record
                existing = db.fetchone(
                    """
                    SELECT id FROM programs 
                    WHERE channel_id = ? AND start_time = ? AND end_time = ?
                    """,
                    (logical_channel_id, new_start, new_end),
                )

                if existing:
                    db.execute(
                        """
                        UPDATE programs
                        SET title = ?, subtitle = ?, description = ?,
                            category = ?, ultimate_epg_id = ?, schedule_id = ?,
                            season_num = ?, episode_num = ?, has_episode_info = ?,
                            director = ?, actors = ?, producer = ?,
                            production_year = ?, rating = ?, thumbnail_url = ?, images = ?
                        WHERE id = ?
                        """,
                        (
                            program.title,
                            program.episode_title,
                            program.plot,
                            program.genre,
                            program.epg_id,
                            program.schedule_id,
                            program.season_num,
                            program.episode_num,
                            1 if program.has_episode_info else 0,
                            program.director,
                            json.dumps(program.cast) if program.cast else None,
                            program.producer,
                            str(program.year) if program.year else None,
                            str(program.rating) if program.rating is not None else None,
                            program.thumbnail,
                            json.dumps(program.images) if program.images else None,
                            existing[0],
                        ),
                    )
                    return "updated"

                return "skipped"

        except Exception as e:
            logger.error(f"Failed to insert/update program {program.epg_id}: {e}")
            # Last resort: try a direct INSERT OR REPLACE
            try:
                db.execute(
                    """
                    INSERT OR REPLACE INTO programs (
                        channel_id, provider_id, start_time, end_time,
                        title, subtitle, description,
                        category, ultimate_epg_id, schedule_id,
                        season_num, episode_num, has_episode_info,
                        director, actors, producer,
                        production_year, rating, thumbnail_url, images
                    ) VALUES (
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?
                    )
                    """,
                    (
                        logical_channel_id,
                        provider_id,
                        new_start,
                        new_end,
                        program.title,
                        program.episode_title,
                        program.plot,
                        program.genre,
                        program.epg_id,
                        program.schedule_id,
                        program.season_num,
                        program.episode_num,
                        1 if program.has_episode_info else 0,
                        program.director,
                        json.dumps(program.cast) if program.cast else None,
                        program.producer,
                        str(program.year) if program.year else None,
                        str(program.rating) if program.rating is not None else None,
                        program.thumbnail,
                        json.dumps(program.images) if program.images else None,
                    ),
                )
                logger.debug(f"Replaced program at {new_start}: {program.title}")
                return "updated"
            except Exception as e2:
                logger.error(f"INSERT OR REPLACE also failed: {e2}")
                raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_active_channel_rows(db) -> list:
        """Return all enabled channels that have a logical channel mapping, including EPG config."""
        return db.fetchall_as_dict("""
            SELECT
                uc.id                   AS ultimate_channel_id,
                uc.ultimate_channel_id  AS channel_id,
                uc.channel_name,
                up.provider_name,
                ucm.channel_id          AS logical_channel_id,
                pec.future_days,
                pec.past_days,
                pec.chunk_hours
            FROM ultimate_channels uc
            JOIN ultimate_providers up  ON uc.ultimate_provider_id = up.id
            JOIN ultimate_channel_mappings ucm ON uc.id = ucm.ultimate_channel_id
            JOIN providers p ON p.name = up.provider_name AND p.source_type = 'ultimate_backend'
            LEFT JOIN provider_epg_config pec ON p.id = pec.provider_id
            WHERE uc.enabled = 1 AND up.enabled = 1
        """)

    @staticmethod
    def _compile_stats(rows: list, results: tuple, label: str) -> Dict:
        """Aggregate per-channel results into a top-level stats dict."""
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
            f"{label} complete: {stats['successful']}/{stats['total_channels']} channels, "
            f"inserted={stats['total_programs_inserted']}, "
            f"updated={stats['total_programs_updated']}, "
            f"skipped={stats['total_programs_skipped']}"
        )

        return stats

    @staticmethod
    def _parse_datetime_safe(value: Optional[str]) -> Optional[datetime]:
        """Parse an ISO datetime string, returning None on any failure."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None

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
        """Persist chunk statistics to import_batches."""
        if not batch_id:
            return

        db = get_db()
        db.execute(
            """
            UPDATE import_batches
            SET programs_fetched   = ?,
                programs_inserted  = ?,
                programs_updated   = ?,
                programs_skipped   = ?,
                duration_ms        = ?,
                status             = ?,
                error_message      = ?
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
