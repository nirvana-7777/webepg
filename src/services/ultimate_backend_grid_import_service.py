"""
Grid import service for Ultimate Backend.

Fetches the lightweight /epg/grid endpoint (all channels, one call per
provider per time window) and stores basic schedule data with
has_details=0, import_source='ultimate_grid'. Detail enrichment is a
separate pass (see ultimate_backend_detail_enrichment_service.py).
"""

import logging
import pytz
from datetime import datetime, timedelta, timezone
from typing import Dict

from ..clients.ultimate_backend_client import UltimateBackendClient
from ..database.connection import get_db

logger = logging.getLogger(__name__)

# Vienna timezone for chunk alignment
VIENNA_TZ = pytz.timezone("Europe/Vienna")


class UltimateBackendGridImportService:
    """Fast grid import using the all-channels /epg/grid endpoint."""

    def __init__(
        self,
        client: UltimateBackendClient,
        chunk_hours: int = 3,
        days_ahead: int = 7,
    ):
        self.client = client
        self.chunk_hours = chunk_hours
        self.days_ahead = days_ahead

    def _get_chunks(self, start: datetime, end: datetime):
        """
        Yield 3-hour chunks aligned to Vienna timezone (0, 3, 6, 9, 12, 15, 18, 21).

        This matches backend cache boundaries for efficiency.
        """
        # Convert to Vienna timezone for alignment
        start_vienna = start.astimezone(VIENNA_TZ)
        end_vienna = end.astimezone(VIENNA_TZ)

        # Round start up to next aligned hour
        hour = start_vienna.hour
        hours_to_next = (
            self.chunk_hours - (hour % self.chunk_hours)
        ) % self.chunk_hours
        next_aligned = start_vienna.replace(minute=0, second=0, microsecond=0)
        if hours_to_next > 0 or start_vienna.minute > 0 or start_vienna.second > 0:
            next_aligned += timedelta(hours=hours_to_next)

        current = next_aligned
        while current < end_vienna:
            chunk_end = min(current + timedelta(hours=self.chunk_hours), end_vienna)
            yield current, chunk_end
            current = chunk_end

    @staticmethod
    def _upsert_program_from_grid(
        item: Dict,
        provider_channel_id: str,
        logical_channel_id: int,
        provider_id: int,
    ) -> str:
        """
        Insert or update a program from one grid item.

        Mapping (per Ultimate Backend API):
            program_id   -> schedule_id    (stable string id, used to fetch details)
            broadcast_id -> ultimate_epg_id (synthetic Kodi-facing int)

        Returns 'inserted', 'updated', or 'skipped'.
        """
        db = get_db()

        broadcast_id = item.get("broadcast_id")
        program_id = item.get("program_id")
        title = item.get("title")
        start_ts = item.get("start")
        end_ts = item.get("end")

        if not all([broadcast_id, program_id, title, start_ts, end_ts]):
            logger.warning(
                f"Incomplete grid item on channel {provider_channel_id}: {item}"
            )
            return "skipped"

        new_start = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
        new_end = datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()
        now = datetime.now(timezone.utc).isoformat()

        existing = db.fetchone(
            "SELECT id FROM programs WHERE channel_id = ? AND start_time = ? AND end_time = ?",
            (logical_channel_id, new_start, new_end),
        )

        if existing:
            db.execute(
                """
                UPDATE programs
                SET title           = COALESCE(?, title),
                    subtitle        = COALESCE(?, subtitle),
                    category        = COALESCE(?, category),
                    production_year = COALESCE(?, production_year),
                    ultimate_epg_id = COALESCE(?, ultimate_epg_id),
                    schedule_id     = COALESCE(?, schedule_id),
                    import_source   = COALESCE(import_source, ?),
                    grid_fetched_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    item.get("episode_name"),
                    item.get("genre_description"),
                    str(item["year"]) if item.get("year") else None,
                    str(broadcast_id),
                    program_id,
                    "ultimate_grid",
                    now,
                    existing[0],
                ),
            )
            return "updated"

        db.execute(
            """
            INSERT OR IGNORE INTO programs (
                channel_id, provider_id, start_time, end_time,
                title, subtitle, category, production_year,
                ultimate_epg_id, schedule_id,
                import_source, has_details, grid_fetched_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                logical_channel_id,
                provider_id,
                new_start,
                new_end,
                title,
                item.get("episode_name"),
                item.get("genre_description"),
                str(item["year"]) if item.get("year") else None,
                str(broadcast_id),
                program_id,
                "ultimate_grid",
                now,
                now,
            ),
        )

        changes = db.fetchone("SELECT changes()")
        return "inserted" if changes and changes[0] > 0 else "skipped"

    async def grid_import_provider(self, provider_name: str) -> Dict:
        """Import grid data for all channels of a provider, one call per chunk."""
        logger.info(f"Starting grid import for provider: {provider_name}")
        db = get_db()

        provider_row = db.fetchone(
            "SELECT id FROM providers WHERE name = ? AND source_type = 'ultimate_backend'",
            (provider_name,),
        )
        if not provider_row:
            logger.warning(f"Provider {provider_name} not found in database")
            return {"error": "Provider not found"}
        provider_id = provider_row[0]

        # provider_channel_id -> logical_channel_id, for all mapped channels
        channel_map = {
            row["channel_id"]: row["logical_channel_id"]
            for row in db.fetchall_as_dict(
                """
                SELECT uc.ultimate_channel_id AS channel_id,
                       ucm.channel_id AS logical_channel_id
                FROM ultimate_channels uc
                JOIN ultimate_channel_mappings ucm ON uc.id = ucm.ultimate_channel_id
                JOIN ultimate_providers up ON uc.ultimate_provider_id = up.id
                WHERE up.provider_name = ? AND uc.enabled = 1
                """,
                (provider_name,),
            )
        }

        if not channel_map:
            logger.warning(f"No mapped channels for provider {provider_name}")
            return {"provider": provider_name, "channels": 0, "total_programs": 0}

        now = datetime.now(timezone.utc)
        end_time = now + timedelta(days=self.days_ahead)

        stats = {
            "provider": provider_name,
            "chunks": 0,
            "channels_in_grid": 0,
            "programs_inserted": 0,
            "programs_updated": 0,
            "programs_skipped": 0,
            "errors": [],
        }

        for chunk_start, chunk_end in self._get_chunks(now, end_time):
            try:
                grid = await self.client.get_epg_grid(
                    provider_name=provider_name,
                    start_time=chunk_start,
                    end_time=chunk_end,
                )
                stats["chunks"] += 1

                for provider_channel_id, items in grid.items():
                    logical_channel_id = channel_map.get(provider_channel_id)
                    if logical_channel_id is None:
                        continue  # channel not mapped/enabled, skip

                    for item in items:
                        try:
                            result = self._upsert_program_from_grid(
                                item,
                                provider_channel_id,
                                logical_channel_id,
                                provider_id,
                            )
                            stats[f"programs_{result}"] = (
                                stats.get(f"programs_{result}", 0) + 1
                            )
                        except Exception as e:
                            stats["errors"].append(
                                f"{provider_channel_id}/{item.get('program_id')}: {e}"
                            )

            except Exception as e:
                stats["errors"].append(f"Chunk {chunk_start}->{chunk_end}: {e}")
                logger.error(f"Grid chunk failed for {provider_name}: {e}")

        logger.info(f"Grid import complete for {provider_name}: {stats}")
        return stats

    async def grid_import_all(self) -> Dict:
        """Import grid data for all enabled Ultimate Backend providers."""
        db = get_db()
        providers = db.fetchall("""
            SELECT p.name FROM providers p
            JOIN ultimate_providers up ON p.name = up.provider_name
            WHERE p.source_type = 'ultimate_backend' AND p.enabled = 1 AND up.enabled = 1
            """)

        results = []
        try:
            for (provider_name,) in providers:
                results.append(await self.grid_import_provider(provider_name))
        finally:
            await self.client.close()

        return {"providers": results, "total_providers": len(providers)}
