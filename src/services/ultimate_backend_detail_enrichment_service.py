"""
Detail enrichment service for Ultimate Backend.

Calls /programs/{program_id} for grid-imported programs that lack
description/cast/crew, using schedule_id (== program_id) as the lookup key.

Runs one concurrent task per grid-EPG provider, each with its own
UltimateBackendClient (own session, own rate limiter). This avoids two
problems with a single shared client:

  1. Providers no longer compete for one global rate-limited request
     budget — a high-volume provider (e.g. magentaeu_hr) can no longer
     starve smaller providers just by having more programs earlier in
     the queue.
  2. A provider that needs extra caution (e.g. HR's stricter Akamai WAF
     validation) can get its own throttle without slowing down every
     other provider's enrichment.

Each provider task pages through its own backlog (has_details = 0,
import_source = 'ultimate_grid') until either the backlog is empty or a
wall-clock deadline is reached, rather than stopping at a fixed row
count. A fixed batch_limit either wastes headroom (set too high for a
quiet provider) or silently truncates a run (set too low for a busy one,
which is exactly how this bug first showed up). Since the query is
already ORDER BY start_time ASC, hitting the deadline still means the
soonest-airing programs were prioritized.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from ..clients.ultimate_backend_client import (
    BatchNotSupported,
    UltimateBackendClient,
)
from ..database.connection import get_db
from ..utils.time_utils import next_local_hour_utc
from .person_service import PersonService

logger = logging.getLogger(__name__)

# Rows fetched per DB round-trip within one provider's task. Small enough
# that a deadline hit mid-page doesn't waste much already-fetched work,
# large enough to keep DB round-trips from dominating runtime relative to
# the (much slower) rate-limited API calls.
PAGE_SIZE = 200

# Upper bound for ids per batch request; matches the backend's limit.
MAX_BATCH_SIZE = 100

# Marks an id whose fetch raised (as opposed to None = provider has no details).
_FETCH_ERROR = object()


class UltimateBackendDetailEnrichmentService:
    """Enrich grid-imported programs with full metadata, per provider."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout_seconds: int = 30,
        max_retries: int = 3,
        requests_per_second: float = 0.5,  # 1 request / 2s, per provider
        max_days: int = 7,
        max_attempts: int = 3,
        run_deadline_hour: int = 9,
        run_deadline_tz: str = "Europe/Vienna",
        batch_size: int = 1,
        provider_overrides: Optional[Dict[str, Dict]] = None,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.requests_per_second = requests_per_second
        self.max_days = max_days
        self.max_attempts = max_attempts
        self.run_deadline_hour = run_deadline_hour
        self.run_deadline_tz = run_deadline_tz
        self.batch_size = batch_size
        self.provider_overrides = provider_overrides or {}

    def _make_client(self, provider_name: Optional[str] = None) -> UltimateBackendClient:
        """New client per provider — own session, own rate limiter."""
        rps = self.requests_per_second
        if provider_name:
            rps = self._provider_setting(provider_name, "max_requests_per_second", rps)
        return UltimateBackendClient(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            requests_per_second=rps,
        )

    def _get_deadline(self) -> datetime:
        """
        Next occurrence of run_deadline_hour in run_deadline_tz, as a UTC
        datetime. Computed once per overall run (not per provider) so all
        provider tasks share the same cutoff.
        """
        return next_local_hour_utc(self.run_deadline_hour, self.run_deadline_tz)

    @staticmethod
    def _get_grid_providers() -> List[str]:
        """Providers currently carrying an un-enriched grid-imported backlog."""
        db = get_db()
        rows = db.fetchall(
            """
            SELECT DISTINCT pr.name
            FROM programs p
            JOIN providers pr ON p.provider_id = pr.id
            WHERE p.import_source = 'ultimate_grid'
              AND p.has_details = 0
            """
        )
        return [row[0] for row in rows]

    @staticmethod
    def _apply_details(provider_id: int, schedule_id: str, details: Dict) -> int:
        """
        Write details to every not-yet-enriched grid row of this provider that
        shares the schedule_id (re-airings, the same programme on several
        channels). Returns the number of rows updated.
        """
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        season_number = details.get("season_number")
        episode_number = details.get("episode_number")
        has_episode = (
            1 if season_number is not None and episode_number is not None else None
        )
        genres_list = details.get("genres") or []
        genres_value = ",".join(genres_list) if genres_list else None

        # Prefer the *_details lists ({id, name[, character_name]}) so the
        # person ids are kept for every role; fall back to the flat name
        # lists for older payloads.
        cast_payload = details.get("cast_details") or details.get("cast")
        directors_payload = details.get("directors_details") or details.get("directors")
        writers_payload = details.get("writers_details") or details.get("writers")
        producers_payload = details.get("producers_details") or details.get("producers")
        presenters_payload = (
            details.get("presenter_details")
            or details.get("presenters_details")
            or details.get("presenters")
        )

        # country_of_origin comes back as a list; the column is scalar.
        country_list = details.get("country_of_origin") or []
        country_value = country_list[0] if country_list else None

        audio_langs = details.get("audio_languages") or []
        subtitle_langs = details.get("subtitle_languages") or []

        db.execute(
            """
            UPDATE programs
            SET description     = ?,
                subtitle        = COALESCE(?, subtitle),
                icon_url        = COALESCE(?, icon_url),
                actors          = COALESCE(?, actors),
                directors       = COALESCE(?, directors),
                writers         = COALESCE(?, writers),
                producers       = COALESCE(?, producers),
                presenters      = COALESCE(?, presenters),
                production_year = COALESCE(?, production_year),
                season_num      = COALESCE(?, season_num),
                episode_num     = COALESCE(?, episode_num),
                has_episode_info = COALESCE(?, has_episode_info),
                category        = COALESCE(?, category),
                country         = COALESCE(?, country),
                series_id       = COALESCE(?, series_id),
                is_adult        = COALESCE(?, is_adult),
                audio_languages = COALESCE(?, audio_languages),
                subtitle_languages = COALESCE(?, subtitle_languages),
                has_details     = 1,
                enriched_at     = ?,
                detail_fetch_attempts = detail_fetch_attempts + 1
            WHERE provider_id = ?
              AND schedule_id = ?
              AND import_source = 'ultimate_grid'
              AND has_details = 0
            """,
            (
                details.get("description"),
                details.get("episode_name"),
                details.get("icon"),
                json.dumps(cast_payload) if cast_payload else None,
                json.dumps(directors_payload) if directors_payload else None,
                json.dumps(writers_payload) if writers_payload else None,
                json.dumps(producers_payload) if producers_payload else None,
                json.dumps(presenters_payload) if presenters_payload else None,
                str(details["year"]) if details.get("year") is not None else None,
                season_number,
                episode_number,
                has_episode,
                genres_value,
                country_value,
                details.get("series_id"),
                (
                    1 if details.get("is_adult") is True
                    else 0 if details.get("is_adult") is False
                    else None
                ),
                json.dumps(audio_langs) if audio_langs else None,
                json.dumps(subtitle_langs) if subtitle_langs else None,
                now,
                provider_id,
                schedule_id,
            ),
        )
        changes = db.fetchone("SELECT changes()")
        rows_updated = changes[0] if changes else 0

        # Register persons (id + name) so their image/roles get fetched
        # once by the person enrichment job.
        PersonService.register(
            provider_id,
            cast_payload,
            directors_payload,
            writers_payload,
            producers_payload,
            presenters_payload,
        )
        return rows_updated

    @staticmethod
    def _mark_failed(provider_id: int, schedule_id: str) -> None:
        """Count a failed or empty lookup as an attempt for all rows of the id."""
        get_db().execute(
            """
            UPDATE programs
            SET detail_fetch_attempts = detail_fetch_attempts + 1
            WHERE provider_id = ? AND schedule_id = ?
              AND import_source = 'ultimate_grid' AND has_details = 0
            """,
            (provider_id, schedule_id),
        )

    def _provider_setting(self, provider_name: str, key: str, default):
        """Per-provider override from config ``detail_enrichment.providers``."""
        return (self.provider_overrides.get(provider_name) or {}).get(key, default)

    async def _fetch_details(
        self,
        client: UltimateBackendClient,
        provider_name: str,
        schedule_ids: List[str],
        use_batch: bool,
    ):
        """
        Fetch details for the given ids.

        Returns (results, use_batch, requests). results maps id -> details
        dict, None (provider has none) or _FETCH_ERROR. use_batch comes back
        False if the backend turned out not to support batching, so the
        caller stops trying for the rest of the run.
        """
        if use_batch and len(schedule_ids) > 1:
            try:
                results = await client.get_program_details_batch(
                    provider_name, schedule_ids
                )
                return results, True, 1
            except BatchNotSupported as e:
                logger.warning(f"{e}; falling back to single requests")
                use_batch = False
            except Exception as e:
                logger.warning(
                    f"Batch detail fetch failed for {provider_name} "
                    f"({len(schedule_ids)} ids): {e}"
                )
                return {sid: _FETCH_ERROR for sid in schedule_ids}, True, 1

        results = {}
        for sid in schedule_ids:
            try:
                results[sid] = await client.get_program_details(
                    provider_name=provider_name, program_id=sid
                )
            except Exception as e:
                logger.warning(f"Failed to fetch details for {sid} ({provider_name}): {e}")
                results[sid] = _FETCH_ERROR
        return results, use_batch, len(schedule_ids)

    async def _enrich_provider(self, provider_name: str, deadline: datetime) -> Dict:
        """Enrich one provider's backlog until it's empty or the deadline hits."""
        client = self._make_client(provider_name)
        db = get_db()
        stats = {
            "provider": provider_name,
            "enriched": 0,  # distinct schedule_ids with details
            "rows_updated": 0,  # airings those details were written to
            "failed": 0,  # distinct schedule_ids without details this run
            "requests": 0,
            "hit_deadline": False,
        }

        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=self.max_days)

        batch_size = max(
            1,
            min(
                int(self._provider_setting(provider_name, "batch_size", self.batch_size)),
                MAX_BATCH_SIZE,
            ),
        )
        use_batch = batch_size > 1

        # Keyset cursor over (start_time, id). Rows that failed stay in the
        # backlog (attempts < max) but are never selected again in this run.
        last_start, last_id = "", 0
        failed_ids = set()

        try:
            while True:
                if datetime.now(timezone.utc) >= deadline:
                    stats["hit_deadline"] = True
                    logger.warning(
                        f"Enrichment deadline reached for {provider_name}; "
                        f"stopping with backlog remaining"
                    )
                    break

                programs = db.fetchall_as_dict(
                    """
                    SELECT p.id, p.provider_id, p.schedule_id, p.start_time
                    FROM programs p
                    JOIN providers pr ON p.provider_id = pr.id
                    WHERE p.import_source = 'ultimate_grid'
                      AND pr.name = ?
                      AND p.has_details = 0
                      AND p.start_time BETWEEN ? AND ?
                      AND p.schedule_id IS NOT NULL
                      AND p.detail_fetch_attempts < ?
                      AND (p.start_time > ? OR (p.start_time = ? AND p.id > ?))
                    ORDER BY p.start_time ASC, p.id ASC
                    LIMIT ?
                    """,
                    (
                        provider_name,
                        now.isoformat(),
                        cutoff.isoformat(),
                        self.max_attempts,
                        last_start,
                        last_start,
                        last_id,
                        PAGE_SIZE,
                    ),
                )

                if not programs:
                    break  # backlog cleared for this provider

                last_start, last_id = programs[-1]["start_time"], programs[-1]["id"]
                provider_id = programs[0]["provider_id"]

                # One request per distinct schedule_id, in start_time order.
                schedule_ids = [
                    sid
                    for sid in dict.fromkeys(p["schedule_id"] for p in programs)
                    if sid not in failed_ids
                ]

                idx = 0
                while idx < len(schedule_ids):
                    if datetime.now(timezone.utc) >= deadline:
                        stats["hit_deadline"] = True
                        break

                    step = batch_size if use_batch else 1
                    chunk = schedule_ids[idx : idx + step]
                    idx += step

                    results, use_batch, requests = await self._fetch_details(
                        client, provider_name, chunk, use_batch
                    )
                    stats["requests"] += requests

                    for sid in chunk:
                        details = results.get(sid)
                        if not details or details is _FETCH_ERROR:
                            # Failed or empty: count an attempt so it is not
                            # re-requested every night for the whole window.
                            self._mark_failed(provider_id, sid)
                            failed_ids.add(sid)
                            stats["failed"] += 1
                            continue
                        try:
                            stats["rows_updated"] += self._apply_details(
                                provider_id, sid, details
                            )
                            stats["enriched"] += 1
                        except Exception as e:
                            logger.warning(f"Failed to store details for {sid}: {e}")
                            self._mark_failed(provider_id, sid)
                            failed_ids.add(sid)
                            stats["failed"] += 1

                if stats["hit_deadline"]:
                    break
        finally:
            await client.close()

        return stats

    async def enrich_all_providers(self) -> Dict:
        """
        Run one concurrent enrichment task per grid-EPG provider.

        Wall-clock for the whole run is bounded by the slowest provider
        (or the shared deadline, whichever comes first) — not the sum of
        all providers — since each runs on its own client/rate limiter.
        """
        providers = self._get_grid_providers()
        if not providers:
            logger.info("No grid-EPG providers with pending enrichment backlog")
            return {"providers": []}

        deadline = self._get_deadline()
        logger.info(
            f"Starting per-provider detail enrichment for {providers}, "
            f"deadline={deadline.isoformat()}"
        )

        tasks = [self._enrich_provider(name, deadline) for name in providers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        compiled = []
        for name, result in zip(providers, results):
            if isinstance(result, Exception):
                logger.error(
                    f"Enrichment task failed for {name}: {result}", exc_info=result
                )
                compiled.append({"provider": name, "error": str(result)})
            else:
                compiled.append(result)

        logger.info(f"Detail enrichment complete: {compiled}")
        return {"providers": compiled}