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
from zoneinfo import ZoneInfo

from ..clients.ultimate_backend_client import UltimateBackendClient
from ..database.connection import get_db

logger = logging.getLogger(__name__)

# Rows fetched per DB round-trip within one provider's task. Small enough
# that a deadline hit mid-page doesn't waste much already-fetched work,
# large enough to keep DB round-trips from dominating runtime relative to
# the (much slower) rate-limited API calls.
PAGE_SIZE = 200


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

    def _make_client(self) -> UltimateBackendClient:
        """New client per provider — own session, own rate limiter."""
        return UltimateBackendClient(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            requests_per_second=self.requests_per_second,
        )

    def _get_deadline(self) -> datetime:
        """
        Next occurrence of run_deadline_hour in run_deadline_tz, as a UTC
        datetime. Computed once per overall run (not per provider) so all
        provider tasks share the same cutoff.
        """
        tz = ZoneInfo(self.run_deadline_tz)
        now_local = datetime.now(tz)
        deadline_local = now_local.replace(
            hour=self.run_deadline_hour, minute=0, second=0, microsecond=0
        )
        if deadline_local <= now_local:
            deadline_local += timedelta(days=1)
        return deadline_local.astimezone(timezone.utc)

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
    async def _enrich_program(
        client: UltimateBackendClient,
        program_id: int,
        provider_name: str,
        schedule_id: str,
    ) -> bool:
        """schedule_id here is the grid program_id string, e.g. 'gn.tv-...'."""
        db = get_db()
        try:
            details = await client.get_program_details(
                provider_name=provider_name,
                program_id=schedule_id,
            )
            if not details:
                logger.debug(f"No details for {schedule_id}")
                return False

            now = datetime.now(timezone.utc).isoformat()
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
                    has_details     = 1,
                    enriched_at     = ?,
                    detail_fetch_attempts = detail_fetch_attempts + 1
                WHERE id = ?
                """,
                (
                    details.get("description"),
                    details.get("episode_name"),
                    details.get("icon"),
                    json.dumps(details["cast"]) if details.get("cast") else None,
                    json.dumps(details["directors"]) if details.get("directors") else None,
                    json.dumps(details["writers"]) if details.get("writers") else None,
                    json.dumps(details["producers"]) if details.get("producers") else None,
                    json.dumps(details["presenter"]) if details.get("presenter") else None,
                    str(details["year"]) if details.get("year") else None,
                    now,
                    program_id,
                ),
            )
            return True

        except Exception as e:
            db.execute(
                "UPDATE programs SET detail_fetch_attempts = detail_fetch_attempts + 1 WHERE id = ?",
                (program_id,),
            )
            logger.warning(f"Failed to enrich {schedule_id} ({provider_name}): {e}")
            return False

    async def _enrich_provider(self, provider_name: str, deadline: datetime) -> Dict:
        """Enrich one provider's backlog until it's empty or the deadline hits."""
        client = self._make_client()
        db = get_db()
        stats = {
            "provider": provider_name,
            "enriched": 0,
            "failed": 0,
            "hit_deadline": False,
        }

        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=self.max_days)

        already_tried = 0

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
                    SELECT p.id, p.schedule_id
                    FROM programs p
                    JOIN providers pr ON p.provider_id = pr.id
                    WHERE p.import_source = 'ultimate_grid'
                      AND pr.name = ?
                      AND p.has_details = 0
                      AND p.start_time BETWEEN ? AND ?
                      AND p.schedule_id IS NOT NULL
                      AND p.detail_fetch_attempts < ?
                    ORDER BY p.start_time ASC, p.id ASC
                    LIMIT ? OFFSET ?
                    """,
                    (
                        provider_name,
                        now.isoformat(),
                        cutoff.isoformat(),
                        self.max_attempts,
                        PAGE_SIZE,
                        already_tried,
                    ),
                )

                if not programs:
                    break  # backlog cleared for this provider

                for program in programs:
                    if datetime.now(timezone.utc) >= deadline:
                        stats["hit_deadline"] = True
                        break
                    ok = await self._enrich_program(
                        client, program["id"], provider_name, program["schedule_id"]
                    )
                    stats["enriched" if ok else "failed"] += 1
                    if not ok:
                        already_tried += 1

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