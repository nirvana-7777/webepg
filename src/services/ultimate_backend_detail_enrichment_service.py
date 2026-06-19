"""
Detail enrichment service for Ultimate Backend.

Calls /programs/{program_id} for grid-imported programs that lack
description/cast/crew, using schedule_id (== program_id) as the lookup key.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from ..clients.ultimate_backend_client import UltimateBackendClient
from ..database.connection import get_db

logger = logging.getLogger(__name__)


class UltimateBackendDetailEnrichmentService:
    """Enrich grid-imported programs with full metadata."""

    def __init__(
        self,
        client: UltimateBackendClient,
        max_days: int = 7,
        max_attempts: int = 3,
        batch_limit: int = 5000,
    ):
        self.client = client
        self.max_days = max_days
        self.max_attempts = max_attempts
        self.batch_limit = batch_limit

    async def _enrich_program(
        self, program_id: int, provider_name: str, schedule_id: str
    ) -> bool:
        """schedule_id here is the grid program_id string, e.g. 'gn.tv-...'."""
        db = get_db()
        try:
            details = await self.client.get_program_details(
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
                    icon_url        = COALESCE(?, icon_url),
                    actors          = ?,
                    directors       = ?,
                    writers         = ?,
                    producers       = ?,
                    production_year = COALESCE(?, production_year),
                    has_details     = 1,
                    enriched_at     = ?,
                    detail_fetch_attempts = detail_fetch_attempts + 1
                WHERE id = ?
                """,
                (
                    details.get("description"),
                    details.get("icon"),
                    json.dumps(details["cast"]) if details.get("cast") else None,
                    (
                        json.dumps(details["directors"])
                        if details.get("directors")
                        else None
                    ),
                    json.dumps(details["writers"]) if details.get("writers") else None,
                    (
                        json.dumps(details["producers"])
                        if details.get("producers")
                        else None
                    ),
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
            logger.warning(f"Failed to enrich {schedule_id}: {e}")
            return False

    async def enrich_programs(self, days_ahead: Optional[int] = None) -> Dict:
        """Enrich up to days_ahead worth of grid-imported, un-enriched programs."""
        days_ahead = days_ahead or self.max_days
        db = get_db()

        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days_ahead)

        programs = db.fetchall_as_dict(
            """
            SELECT p.id, p.schedule_id, pr.name AS provider_name
            FROM programs p
            JOIN providers pr ON p.provider_id = pr.id
            WHERE p.import_source = 'ultimate_grid'
              AND p.has_details = 0
              AND p.start_time BETWEEN ? AND ?
              AND p.schedule_id IS NOT NULL
              AND p.detail_fetch_attempts < ?
            ORDER BY p.start_time ASC
            LIMIT ?
            """,
            (now.isoformat(), cutoff.isoformat(), self.max_attempts, self.batch_limit),
        )

        stats = {"total": len(programs), "enriched": 0, "failed": 0}
        try:
            for program in programs:
                ok = await self._enrich_program(
                    program["id"], program["provider_name"], program["schedule_id"]
                )
                stats["enriched" if ok else "failed"] += 1
        finally:
            await self.client.close()

        logger.info(f"Enrichment complete: {stats}")
        return stats
