"""
Background job scheduler for EPG service.
"""

import asyncio
import logging
from datetime import datetime
from threading import Thread
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..services.cleanup_service import CleanupService
from ..services.import_service import ImportService
from ..services.ultimate_backend_discovery_service import (
    UltimateBackendDiscoveryService,
)
from ..services.ultimate_backend_import_service import UltimateBackendImportService
from ..services.ultimate_backend_grid_import_service import (
    UltimateBackendGridImportService,
)
from ..services.ultimate_backend_detail_enrichment_service import (
    UltimateBackendDetailEnrichmentService,
)

logger = logging.getLogger(__name__)


class JobScheduler:
    """Manages background jobs for imports and cleanup."""

    def __init__(self, config: dict):
        self.config = config
        self.scheduler = BackgroundScheduler(timezone=config.get("timezone", "UTC"))
        self.import_service = ImportService()
        self.cleanup_service = CleanupService()

        self.ultimate_enabled = config.get("ultimate_backend", {}).get("enabled", False)
        self.ultimate_client = None
        self.ultimate_discovery_service = None
        self._ultimate_import_service: Optional[UltimateBackendImportService] = None
        self.grid_import_service: Optional[UltimateBackendGridImportService] = None
        self.detail_enrichment_service: Optional[
            UltimateBackendDetailEnrichmentService
        ] = None

        if self.ultimate_enabled:
            self._init_ultimate_backend(config)

    def _init_ultimate_backend(self, config: dict):
        """Initialize Ultimate Backend client, discovery, and import service."""
        from ..clients.ultimate_backend_client import UltimateBackendClient

        ub_config = config.get("ultimate_backend", {})
        instance = ub_config.get("instance", {})
        import_config = ub_config.get("import", {})
        grid_config = ub_config.get("grid_import", {})
        detail_config = ub_config.get("detail_enrichment", {})

        self.ultimate_client = UltimateBackendClient(
            base_url=instance.get("base_url", "http://ultimate:7777"),
            api_key=instance.get("api_key"),
            timeout_seconds=import_config.get("timeout_seconds", 30),
            max_retries=import_config.get("max_retries", 3),
            requests_per_second=import_config.get("max_requests_per_second", 5),
        )

        self.ultimate_discovery_service = UltimateBackendDiscoveryService(
            self.ultimate_client
        )

        # api_max_future_days reflects the hard cap of the Ultimate Backend API
        # (currently 3 days).  Setting future_days higher than this is harmless
        # but wastes requests; setting it lower is fine for shorter windows.
        self._ultimate_import_service = UltimateBackendImportService(
            client=self.ultimate_client,
            future_days=import_config.get("future_days", 3),
            past_days=import_config.get("past_days", 7),
            chunk_hours=import_config.get("chunk_hours", 24),
            max_concurrent_channels=import_config.get("max_concurrent_channels", 3),
            api_max_future_days=import_config.get("api_max_future_days", 3),
        )

        self.grid_import_service = UltimateBackendGridImportService(
            client=self.ultimate_client,
            chunk_hours=grid_config.get("chunk_hours", 3),
            days_ahead=grid_config.get("days_ahead", 7),
        )

        self.detail_enrichment_service = UltimateBackendDetailEnrichmentService(
            client=self.ultimate_client,
            max_days=detail_config.get("max_days", 7),
            max_attempts=detail_config.get("max_attempts", 3),
        )

        logger.info("Ultimate Backend integration initialized")

    # ------------------------------------------------------------------
    # XMLTV jobs
    # ------------------------------------------------------------------

    def _run_import_job(self):
        """Execute daily XMLTV import for all providers."""
        logger.info("Starting scheduled XMLTV import job")
        try:
            logs = self.import_service.import_all_providers()
            success_count = sum(1 for log in logs if log.status == "success")
            failed_count = len(logs) - success_count
            logger.info(
                f"XMLTV import completed: {success_count} succeeded, {failed_count} failed"
            )
            self._run_cleanup_job()
        except Exception as e:
            logger.error(f"XMLTV import job failed: {e}", exc_info=True)

    def _run_cleanup_job(self):
        """Delete programs older than the configured retention window."""
        logger.info("Starting scheduled cleanup job")
        try:
            retention_days = self.config.get("retention_days", 7)
            deleted_count = self.cleanup_service.cleanup_old_programs(retention_days)
            logger.info(f"Cleanup completed: {deleted_count} programs deleted")
        except Exception as e:
            logger.error(f"Cleanup job failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Ultimate Backend jobs
    # ------------------------------------------------------------------

    def _run_ultimate_discovery_job(self):
        """Discover providers and channels from Ultimate Backend."""
        if not self.ultimate_enabled:
            return
        logger.info("Starting Ultimate Backend discovery job")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stats = loop.run_until_complete(
                self.ultimate_discovery_service.discover_all()
            )
            loop.close()
            logger.info(f"Ultimate Backend discovery completed: {stats}")
        except Exception as e:
            logger.error(f"Ultimate Backend discovery job failed: {e}", exc_info=True)

    def _run_ultimate_incremental_job(self):
        """
        Daily incremental import — only fetches data beyond the stored cursor.

        Each channel advances its last_imported_until forward by one day's
        worth of chunks, so on a healthy daily schedule this typically makes
        one API call per channel.
        """
        if not self.ultimate_enabled:
            return
        logger.info("Starting Ultimate Backend incremental import job")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stats = loop.run_until_complete(
                self._ultimate_import_service.incremental_import_all()
            )
            loop.close()
            logger.info(f"Incremental import completed: {stats}")
            self._run_cleanup_job()
        except Exception as e:
            logger.error(f"Incremental import job failed: {e}", exc_info=True)

    def _run_ultimate_full_job(self):
        """
        Full (bootstrap) import — resets all channel cursors and re-fetches
        everything within past_days history and api_max_future_days future.

        Runs in a daemon thread so it does not block the scheduler.
        """
        if not self.ultimate_enabled:
            return
        logger.info("Starting Ultimate Backend full import job")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stats = loop.run_until_complete(
                self._ultimate_import_service.full_import_all()
            )
            loop.close()
            logger.info(f"Full import completed: {stats}")
        except Exception as e:
            logger.error(f"Full import job failed: {e}", exc_info=True)

    def _run_grid_import_job(self):
        """
        Primary Ultimate Backend grid import.

        After the grid run, inspect per-provider stats.  A provider that
        returned zero programs with no errors has not implemented /epg/grid;
        we fall back to the channel-based import for that provider only.
        Providers with errors are logged but NOT fallen back to automatically
        — an error is a different failure mode (network, auth, …) and
        silently retrying via a slower path would mask it.
        """
        if not self.ultimate_enabled or not self.grid_import_service:
            return
        logger.info("Starting Ultimate Backend grid import job")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stats = loop.run_until_complete(self.grid_import_service.grid_import_all())
            loop.close()
            logger.info(f"Grid import completed: {stats}")
        except Exception as e:
            logger.error(f"Grid import job failed: {e}", exc_info=True)
            return

        for provider_stats in stats.get("providers", []):
            provider_name = provider_stats.get("provider")
            errors = provider_stats.get("errors", [])
            total_programs = (
                provider_stats.get("programs_inserted", 0)
                + provider_stats.get("programs_updated", 0)
                + provider_stats.get("programs_skipped", 0)
            )

            if errors:
                # Real errors — log loudly, don't mask with a fallback.
                logger.error(
                    f"Grid import errors for {provider_name} "
                    f"({len(errors)} error(s)): {errors[:5]}"  # first 5 to avoid log spam
                )
            elif total_programs == 0:
                # Clean empty response → provider doesn't support /epg/grid.
                logger.info(
                    f"Grid returned no programs for {provider_name} "
                    f"(no errors) — triggering channel-based fallback"
                )
                Thread(
                    target=self._run_channel_import_fallback,
                    args=(provider_name,),
                    daemon=True,
                ).start()

    def _run_channel_import_fallback(self, provider_name: str):
        """
        Channel-based fallback import for a single provider.

        Called from _run_grid_import_job when /epg/grid returns empty for
        a provider.  Runs in a daemon thread so it doesn't block the scheduler.
        """
        if not self.ultimate_enabled or not self._ultimate_import_service:
            return
        logger.info(f"Starting channel-based fallback import for {provider_name}")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                self._ultimate_import_service.incremental_import_provider(provider_name)
            )
            loop.close()
            logger.info(f"Channel-based fallback complete for {provider_name}: {result}")
            self._run_cleanup_job()
        except Exception as e:
            logger.error(
                f"Channel-based fallback failed for {provider_name}: {e}", exc_info=True
            )

    def _run_detail_enrichment_job(self):
        """Run detail enrichment for grid-imported Ultimate Backend programs."""
        if not self.ultimate_enabled or not self.detail_enrichment_service:
            return
        logger.info("Starting Ultimate Backend detail enrichment job")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stats = loop.run_until_complete(
                self.detail_enrichment_service.enrich_programs()
            )
            loop.close()
            logger.info(f"Detail enrichment completed: {stats}")
        except Exception as e:
            logger.error(f"Detail enrichment job failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Scheduler lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Register all jobs and start the APScheduler background scheduler."""
        import_time = self.config.get("import_time", "03:00")
        hour, minute = map(int, import_time.split(":"))

        # Daily XMLTV import.
        self.scheduler.add_job(
            self._run_import_job,
            trigger=CronTrigger(hour=hour, minute=minute),
            id="daily_import",
            name="Daily XMLTV Import",
            replace_existing=True,
        )
        logger.info(f"Scheduled daily XMLTV import at {import_time}")

        if self.ultimate_enabled:
            ub_config = self.config.get("ultimate_backend", {})
            discovery_config = ub_config.get("discovery", {})

            # Weekly discovery (default: Sunday at 02:00).
            if discovery_config.get("enabled", True):
                discovery_day = discovery_config.get("day", 6)  # Monday=0, Sunday=6
                discovery_hour = discovery_config.get("hour", 2)
                self.scheduler.add_job(
                    self._run_ultimate_discovery_job,
                    trigger=CronTrigger(
                        day_of_week=discovery_day, hour=discovery_hour, minute=0
                    ),
                    id="weekly_ultimate_discovery",
                    name="Weekly Ultimate Backend Discovery",
                    replace_existing=True,
                )
                logger.info(
                    f"Scheduled weekly Ultimate Backend discovery: "
                    f"day={discovery_day} at {discovery_hour}:00"
                )

            # Grid import: 1 AM Vienna time.
            # Channel-based import is NOT scheduled — it runs as a fallback
            # only when grid_import_all() returns empty for a provider.
            self.scheduler.add_job(
                self._run_grid_import_job,
                trigger=CronTrigger(hour=1, minute=0, timezone="Europe/Vienna"),
                id="daily_grid_import",
                name="Ultimate Backend Grid Import",
                replace_existing=True,
            )
            logger.info("Scheduled daily grid import at 1:00 AM Vienna time")

            # Detail enrichment: 4 AM Vienna time (after grid import).
            self.scheduler.add_job(
                self._run_detail_enrichment_job,
                trigger=CronTrigger(hour=4, minute=0, timezone="Europe/Vienna"),
                id="daily_detail_enrichment",
                name="Ultimate Backend Detail Enrichment",
                replace_existing=True,
            )
            logger.info("Scheduled daily detail enrichment at 4:00 AM Vienna time")

        self.scheduler.start()
        logger.info("Job scheduler started")

    def stop(self):
        """Shut down the scheduler and close the Ultimate Backend HTTP client."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Job scheduler stopped")

        if self.ultimate_client:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.ultimate_client.close())
                loop.close()
            except Exception as e:
                logger.warning(f"Error closing Ultimate Backend client: {e}")

    # ------------------------------------------------------------------
    # Manual triggers
    # ------------------------------------------------------------------

    def trigger_import_now(self):
        """Immediately queue a XMLTV import outside the daily schedule."""
        logger.info("Manually triggering XMLTV import")
        self.scheduler.add_job(
            self._run_import_job,
            id="manual_import",
            name="Manual XMLTV Import",
            replace_existing=True,
        )

    def trigger_ultimate_incremental_now(self, provider_name: Optional[str] = None):
        """
        Immediately run a channel-based import outside the daily schedule.

        Args:
            provider_name: If supplied, only that provider is imported.
                           If None, all providers are imported (same as
                           the old scheduled incremental job).
        """
        if not self.ultimate_enabled:
            logger.warning("Ultimate Backend not enabled")
            return
        if provider_name:
            logger.info(f"Manually triggering channel-based import for {provider_name}")
            Thread(
                target=self._run_channel_import_fallback,
                args=(provider_name,),
                daemon=True,
            ).start()
        else:
            logger.info("Manually triggering Ultimate Backend incremental import (all providers)")
            Thread(target=self._run_ultimate_incremental_job, daemon=True).start()

    def trigger_ultimate_full_now(self):
        """
        Immediately start a full Ultimate Backend import in a daemon thread.

        A full import can take a while (one API call per channel per day chunk),
        so we run it in a background thread rather than blocking the scheduler.
        The import service itself is async-safe: it resets its semaphore at the
        top of full_import_all() before touching the event loop.
        """
        if not self.ultimate_enabled:
            logger.warning("Ultimate Backend not enabled")
            return
        logger.info("Manually triggering Ultimate Backend full import")
        Thread(target=self._run_ultimate_full_job, daemon=True).start()

    def trigger_ultimate_discovery_now(self):
        """Immediately queue an Ultimate Backend discovery run."""
        if not self.ultimate_enabled:
            logger.warning("Ultimate Backend not enabled")
            return
        logger.info("Manually triggering Ultimate Backend discovery")
        self.scheduler.add_job(
            self._run_ultimate_discovery_job,
            id="manual_ultimate_discovery",
            name="Manual Ultimate Backend Discovery",
            replace_existing=True,
        )

    def trigger_grid_import_now(self):
        """Immediately queue a grid import."""
        if not self.ultimate_enabled or not self.grid_import_service:
            logger.warning("Ultimate Backend grid import not enabled")
            return
        logger.info("Manually triggering grid import")
        self.scheduler.add_job(
            self._run_grid_import_job,
            id="manual_grid_import",
            name="Manual Grid Import",
            replace_existing=True,
        )

    def trigger_detail_enrichment_now(self):
        """Immediately queue a detail enrichment run."""
        if not self.ultimate_enabled or not self.detail_enrichment_service:
            logger.warning("Ultimate Backend detail enrichment not enabled")
            return
        logger.info("Manually triggering detail enrichment")
        self.scheduler.add_job(
            self._run_detail_enrichment_job,
            id="manual_detail_enrichment",
            name="Manual Detail Enrichment",
            replace_existing=True,
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_next_run_time(self, job_id: str = "daily_import") -> Optional[datetime]:
        """Return the next scheduled run time for a given job, or None."""
        job = self.scheduler.get_job(job_id)
        return job.next_run_time if job else None

    @property
    def ultimate_import_service(self) -> Optional[UltimateBackendImportService]:
        return self._ultimate_import_service