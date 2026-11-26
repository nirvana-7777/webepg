"""
Person enrichment for Ultimate Backend.

Fetches image and roles for rows in ``persons`` that are still 'pending',
via ``GET /api/providers/{provider}/persons/{person_id}``. Each person is
fetched once and kept (the table is not touched by programme cleanup), so
the load is bounded by the number of distinct persons, not programmes.

Same shape as the detail enrichment: one task per provider with its own
client and rate limiter, running until the backlog is empty or the
wall-clock deadline is reached. Persons are processed in registration order,
which follows detail enrichment's start_time order, so people from the
soonest-airing programmes come first.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from ..clients.ultimate_backend_client import UltimateBackendClient
from ..database.connection import get_db
from ..utils.time_utils import next_local_hour_utc

logger = logging.getLogger(__name__)

PAGE_SIZE = 200


class UltimateBackendPersonEnrichmentService:
    """Fetch image/roles for registered persons, per provider."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout_seconds: int = 30,
        max_retries: int = 3,
        requests_per_second: float = 0.5,
        max_attempts: int = 3,
        run_deadline_hour: int = 9,
        run_deadline_tz: str = "Europe/Vienna",
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.requests_per_second = requests_per_second
        self.max_attempts = max_attempts
        self.run_deadline_hour = run_deadline_hour
        self.run_deadline_tz = run_deadline_tz

    def _make_client(self) -> UltimateBackendClient:
        return UltimateBackendClient(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            requests_per_second=self.requests_per_second,
        )

    def _get_pending_providers(self) -> List[Tuple[int, str]]:
        rows = get_db().fetchall(
            """
            SELECT DISTINCT pe.provider_id, pr.name
            FROM persons pe
            JOIN providers pr ON pe.provider_id = pr.id
            WHERE pe.status = 'pending' AND pe.fetch_attempts < ?
            """,
            (self.max_attempts,),
        )
        return [(row[0], row[1]) for row in rows]

    @staticmethod
    async def _enrich_person(
        client: UltimateBackendClient,
        provider_id: int,
        provider_name: str,
        person_id: str,
    ) -> bool:
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        try:
            person = await client.get_person_details(provider_name, person_id)
            if person is None:
                db.execute(
                    """
                    UPDATE persons
                    SET status = 'not_found', fetched_at = ?,
                        fetch_attempts = fetch_attempts + 1
                    WHERE provider_id = ? AND person_id = ?
                    """,
                    (now, provider_id, person_id),
                )
                return False

            roles = person.get("roles") or []
            db.execute(
                """
                UPDATE persons
                SET name = COALESCE(?, name),
                    image_url = ?,
                    roles = ?,
                    status = 'ok',
                    fetched_at = ?,
                    fetch_attempts = fetch_attempts + 1
                WHERE provider_id = ? AND person_id = ?
                """,
                (
                    person.get("name"),
                    person.get("image"),
                    json.dumps(roles) if roles else None,
                    now,
                    provider_id,
                    person_id,
                ),
            )
            return True

        except Exception as e:
            db.execute(
                "UPDATE persons SET fetch_attempts = fetch_attempts + 1 "
                "WHERE provider_id = ? AND person_id = ?",
                (provider_id, person_id),
            )
            logger.warning(f"Failed to fetch person {person_id} ({provider_name}): {e}")
            return False

    async def _enrich_provider(
        self, provider_id: int, provider_name: str, deadline: datetime
    ) -> Dict:
        client = self._make_client()
        db = get_db()
        stats = {
            "provider": provider_name,
            "fetched": 0,
            "failed": 0,
            "hit_deadline": False,
        }
        # Failed rows stay in the pending set (until max_attempts) and sort
        # first, so skip them with OFFSET instead of retrying within one run.
        failed_this_run = 0

        try:
            while True:
                if datetime.now(timezone.utc) >= deadline:
                    stats["hit_deadline"] = True
                    logger.warning(
                        f"Person enrichment deadline reached for {provider_name}; "
                        f"stopping with backlog remaining"
                    )
                    break

                rows = db.fetchall(
                    """
                    SELECT person_id FROM persons
                    WHERE provider_id = ? AND status = 'pending'
                      AND fetch_attempts < ?
                    ORDER BY rowid ASC
                    LIMIT ? OFFSET ?
                    """,
                    (provider_id, self.max_attempts, PAGE_SIZE, failed_this_run),
                )
                if not rows:
                    break

                for row in rows:
                    if datetime.now(timezone.utc) >= deadline:
                        stats["hit_deadline"] = True
                        break
                    ok = await self._enrich_person(
                        client, provider_id, provider_name, row["person_id"]
                    )
                    if ok:
                        stats["fetched"] += 1
                    else:
                        stats["failed"] += 1
                        # not_found leaves the pending set; failures stay.
                        still_pending = db.fetchone(
                            "SELECT 1 FROM persons WHERE provider_id = ? "
                            "AND person_id = ? AND status = 'pending' "
                            "AND fetch_attempts < ?",
                            (provider_id, row["person_id"], self.max_attempts),
                        )
                        if still_pending:
                            failed_this_run += 1

                if stats["hit_deadline"]:
                    break
        finally:
            await client.close()

        return stats

    async def enrich_all_providers(self) -> Dict:
        providers = self._get_pending_providers()
        if not providers:
            logger.info("No pending persons to enrich")
            return {"providers": []}

        deadline = next_local_hour_utc(self.run_deadline_hour, self.run_deadline_tz)
        logger.info(
            f"Starting person enrichment for {[name for _, name in providers]}, "
            f"deadline={deadline.isoformat()}"
        )

        results = await asyncio.gather(
            *[self._enrich_provider(pid, name, deadline) for pid, name in providers],
            return_exceptions=True,
        )

        compiled = []
        for (_, name), result in zip(providers, results):
            if isinstance(result, Exception):
                logger.error(f"Person enrichment failed for {name}: {result}", exc_info=result)
                compiled.append({"provider": name, "error": str(result)})
            else:
                compiled.append(result)

        logger.info(f"Person enrichment complete: {compiled}")
        return {"providers": compiled}
