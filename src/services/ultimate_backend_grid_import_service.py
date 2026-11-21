# src/services/ultimate_backend_grid_import_service.py

"""
Grid import service for Ultimate Backend.

Fetches the lightweight /epg/grid endpoint (all channels, one call per
provider per time window) and stores basic schedule data with
has_details=0, import_source='ultimate_grid'. Detail enrichment is a
separate pass (see ultimate_backend_detail_enrichment_service.py).

Identity of a stored listing
----------------------------
``epg_event_id`` (per-listing nPVR slot id, e.g. ``pro7_sd_0334511e``) is
persisted on INSERT and UPDATE. It is NOT assumed to be unique per airing:
the example id shares its suffix with the programme guid, so repeats of
the same programme on one channel may reuse it. Matching therefore goes
in this order:

    1. Exact slot: (channel_id, start_time, end_time). Always correct, and
       also the only way to backfill rows that predate the epg_event_id
       column (idx_programs_unique is UNIQUE on this triple, so
       INSERT OR IGNORE would otherwise discard the new id).
    2. Drift: same (channel_id, epg_event_id) AND start within
       EVENT_ID_DRIFT_TOLERANCE of the incoming start. This is the case
       the epg_event_id key exists for (upstream start/end moved by a
       second or a few minutes); the row is updated in place, including
       its times. Step 1 missed, so the new triple is free and
       idx_programs_unique cannot fire. A row with the same epg_event_id
       but a far-away start is a different airing and is never touched.
    3. Otherwise INSERT.

Other behaviour
---------------
    * ``_get_chunks`` floors to the aligned boundary so the current window
      is covered.
    * Incomplete items are counted and logged once per chunk; item errors
      are summarised once per chunk (not once per failing item).
    * Every provider result carries ``ok`` (bool) and, when not ok,
      ``failure`` (reason). ``grid_import_all`` returns ``failed_providers``
      so callers/schedulers can act on a broken run instead of parsing logs.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from ..clients.ultimate_backend_client import UltimateBackendClient
from ..database.connection import get_db

logger = logging.getLogger(__name__)

# Use UTC for chunk alignment to match API expectations.
CHUNK_HOURS = 3

# How far an incoming start may differ from a stored row with the same
# epg_event_id and still count as "the same airing, rescheduled/drifted".
# Must stay well below the gap between repeats of one programme. Tune
# against real grid data.
EVENT_ID_DRIFT_TOLERANCE = timedelta(minutes=10)

# A run is considered broken if more than this fraction of chunks failed
# to fetch, or of seen items failed to store.
MAX_FAILED_FRACTION = 0.25

# Cap on stored error strings in the returned stats / log line.
MAX_ERRORS_KEPT = 20


class UltimateBackendGridImportService:
    """Fast grid import using the all-channels /epg/grid endpoint."""

    def __init__(
        self,
        client: UltimateBackendClient,
        chunk_hours: int = CHUNK_HOURS,
        days_ahead: int = 7,
    ):
        self.client = client
        self.chunk_hours = chunk_hours
        self.days_ahead = days_ahead

    def _get_chunks(self, start: datetime, end: datetime):
        """
        Yield aligned UTC chunks (0, 3, 6, 9, 12, 15, 18, 21).

        Start is floored to the previous aligned boundary so the current
        window is always covered. The final chunk is clipped to ``end``.
        """
        floored = start.replace(minute=0, second=0, microsecond=0)
        floored -= timedelta(hours=floored.hour % self.chunk_hours)

        current = floored
        while current < end:
            chunk_end = min(current + timedelta(hours=self.chunk_hours), end)
            yield current, chunk_end
            current = chunk_end

    @staticmethod
    def _find_drifted_row(
        db, logical_channel_id: int, epg_event_id: str, new_start: datetime
    ) -> Optional[int]:
        """
        Id of the row with this epg_event_id whose start is closest to
        ``new_start`` and within EVENT_ID_DRIFT_TOLERANCE, else None.

        Compared in Python (not SQL) so differing stored timestamp formats
        can't cause false misses; only a handful of rows share one id.
        """
        rows = db.fetchall(
            "SELECT id, start_time FROM programs "
            "WHERE channel_id = ? AND epg_event_id = ?",
            (logical_channel_id, epg_event_id),
        )
        best_id, best_delta = None, None
        for row_id, start_iso in rows:
            try:
                old_start = datetime.fromisoformat(start_iso)
            except (TypeError, ValueError):
                continue
            if old_start.tzinfo is None:
                old_start = old_start.replace(tzinfo=timezone.utc)
            delta = abs(old_start - new_start)
            if delta <= EVENT_ID_DRIFT_TOLERANCE and (
                best_delta is None or delta < best_delta
            ):
                best_id, best_delta = row_id, delta
        return best_id

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
            program_id   -> schedule_id      (stable string id, used to
                                              fetch programme details)
            broadcast_id -> ultimate_epg_id  (synthetic Kodi-facing int)
            epg_event_id -> epg_event_id     (per-listing guid; the value
                                              the nPVR/timer API needs)

        Returns 'inserted', 'updated', 'rescheduled' (matched by
        epg_event_id with drifted times), 'skipped' or 'incomplete'.
        """
        db = get_db()

        broadcast_id = item.get("broadcast_id")
        program_id = item.get("program_id")
        title = item.get("title")
        start_ts = item.get("start")
        end_ts = item.get("end")
        epg_event_id = item.get("epg_event_id")

        if not all([broadcast_id, program_id, title, start_ts, end_ts]):
            return "incomplete"
        if end_ts <= start_ts:
            return "incomplete"

        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        new_start = start_dt.isoformat()
        new_end = datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()
        now = datetime.now(timezone.utc).isoformat()

        outcome = "updated"
        existing_id = None

        # 1. Exact slot.
        row = db.fetchone(
            "SELECT id FROM programs "
            "WHERE channel_id = ? AND start_time = ? AND end_time = ?",
            (logical_channel_id, new_start, new_end),
        )
        if row:
            existing_id = row[0]
        elif epg_event_id:
            # 2. Same epg_event_id, start within tolerance -> drift.
            existing_id = UltimateBackendGridImportService._find_drifted_row(
                db, logical_channel_id, epg_event_id, start_dt
            )
            if existing_id is not None:
                outcome = "rescheduled"

        if existing_id is not None:
            # start/end are written so drift converges; for an exact match
            # they are identical (no-op). The triple is free in the drift
            # case because step 1 missed.
            db.execute(
                """
                UPDATE programs
                SET title           = COALESCE(?, title),
                    subtitle        = COALESCE(?, subtitle),
                    category        = COALESCE(?, category),
                    production_year = COALESCE(?, production_year),
                    ultimate_epg_id = COALESCE(?, ultimate_epg_id),
                    schedule_id     = COALESCE(?, schedule_id),
                    epg_event_id    = COALESCE(?, epg_event_id),
                    start_time      = ?,
                    end_time        = ?,
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
                    epg_event_id,
                    new_start,
                    new_end,
                    "ultimate_grid",
                    now,
                    existing_id,
                ),
            )
            return outcome

        # 3. New airing.
        db.execute(
            """
            INSERT OR IGNORE INTO programs (
                channel_id, provider_id, start_time, end_time,
                title, subtitle, category, production_year,
                ultimate_epg_id, schedule_id, epg_event_id,
                import_source, has_details, grid_fetched_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
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
                epg_event_id,
                "ultimate_grid",
                now,
                now,
            ),
        )

        changes = db.fetchone("SELECT changes()")
        return "inserted" if changes and changes[0] > 0 else "skipped"

    @staticmethod
    def _new_stats(provider_name: str) -> Dict:
        return {
            "provider": provider_name,
            "ok": True,
            "chunks": 0,
            "channels_in_grid": 0,
            "programs_inserted": 0,
            "programs_updated": 0,
            "programs_rescheduled": 0,
            "programs_skipped": 0,
            "programs_incomplete": 0,
            "error_count": 0,
            "errors": [],
        }

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
            return {
                "provider": provider_name,
                "ok": False,
                "failure": "provider not found",
                "error": "Provider not found",
            }
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

        stats = self._new_stats(provider_name)

        if not channel_map:
            logger.warning(f"No mapped channels for provider {provider_name}")
            return stats

        now = datetime.now(timezone.utc)
        end_time = now + timedelta(days=self.days_ahead)

        seen_channels = set()
        attempted_chunks = 0
        failed_chunks = 0
        items_seen = 0
        item_errors = 0

        for chunk_start, chunk_end in self._get_chunks(now, end_time):
            attempted_chunks += 1
            chunk_errors = 0
            chunk_incomplete = 0
            chunk_first_error = ""
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
                    seen_channels.add(provider_channel_id)

                    for item in items:
                        items_seen += 1
                        try:
                            result = self._upsert_program_from_grid(
                                item,
                                provider_channel_id,
                                logical_channel_id,
                                provider_id,
                            )
                            if result == "incomplete":
                                chunk_incomplete += 1
                            else:
                                stats[f"programs_{result}"] += 1
                        except Exception as e:
                            chunk_errors += 1
                            if not chunk_first_error:
                                chunk_first_error = (
                                    f"{provider_channel_id}/"
                                    f"{item.get('program_id')}: {e}"
                                )

                if chunk_incomplete:
                    stats["programs_incomplete"] += chunk_incomplete
                    logger.warning(
                        "Chunk %s->%s: %d incomplete grid item(s) skipped",
                        chunk_start.isoformat(), chunk_end.isoformat(),
                        chunk_incomplete,
                    )

            except Exception as e:
                failed_chunks += 1
                stats["error_count"] += 1
                stats["errors"].append(f"Chunk {chunk_start}->{chunk_end}: {e}")
                logger.error(f"Grid chunk failed for {provider_name}: {e}")

            if chunk_errors:
                item_errors += chunk_errors
                stats["error_count"] += chunk_errors
                stats["errors"].append(
                    f"Chunk {chunk_start}->{chunk_end}: {chunk_errors} item(s) "
                    f"failed; first: {chunk_first_error}"
                )
                logger.error(
                    "Chunk %s->%s: %d item(s) failed; first: %s",
                    chunk_start.isoformat(), chunk_end.isoformat(),
                    chunk_errors, chunk_first_error,
                )

        stats["channels_in_grid"] = len(seen_channels)

        # Keep the returned stats / log line bounded.
        total_error_entries = len(stats["errors"])
        if total_error_entries > MAX_ERRORS_KEPT:
            stats["errors"] = stats["errors"][:MAX_ERRORS_KEPT]
            stats["errors"].append(
                f"... and {total_error_entries - MAX_ERRORS_KEPT} more"
            )

        landed = (
            stats["programs_inserted"]
            + stats["programs_updated"]
            + stats["programs_rescheduled"]
        )

        failure = None
        if stats["chunks"] == 0:
            failure = "no chunk could be fetched"
        elif items_seen == 0:
            failure = "grid contained no items for any mapped channel"
        elif stats["error_count"] and landed == 0:
            failure = "errors occurred and no rows landed"
        elif failed_chunks > attempted_chunks * MAX_FAILED_FRACTION:
            failure = f"{failed_chunks}/{attempted_chunks} chunks failed"
        elif item_errors > items_seen * MAX_FAILED_FRACTION:
            failure = f"{item_errors}/{items_seen} items failed to store"

        if failure:
            stats["ok"] = False
            stats["failure"] = failure
            logger.error(
                "Grid import for %s is BROKEN (%s): %s",
                provider_name, failure, stats,
            )
        else:
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
                try:
                    results.append(await self.grid_import_provider(provider_name))
                except Exception as e:
                    logger.error(f"Grid import for {provider_name} crashed: {e}")
                    results.append(
                        {
                            "provider": provider_name,
                            "ok": False,
                            "failure": "crashed",
                            "error": str(e),
                        }
                    )
        finally:
            await self.client.close()

        return {
            "providers": results,
            "total_providers": len(providers),
            "failed_providers": [
                r.get("provider") for r in results if not r.get("ok", False)
            ],
        }