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

        # Detail enrichment gets its own client construction (base_url/api_key
        # only, no shared instance) because it builds one UltimateBackendClient
        # PER PROVIDER internally — own session, own rate limiter — rather than
        # sharing self.ultimate_client. This keeps a high-volume or WAF-sensitive
        # provider (e.g. magentaeu_hr) from throttling every other provider's
        # discovery/grid/channel-import calls, which all still run at
        # import_config's requests_per_second (default 5) on self.ultimate_client.
        self.detail_enrichment_service = UltimateBackendDetailEnrichmentService(
            base_url=instance.get("base_url", "http://ultimate:7777"),
            api_key=instance.get("api_key"),
            timeout_seconds=import_config.get("timeout_seconds", 30),
            max_retries=import_config.get("max_retries", 3),
            requests_per_second=detail_config.get("max_requests_per_second", 0.5),
            max_days=detail_config.get("max_days", 7),
            max_attempts=detail_config.get("max_attempts", 3),
            run_deadline_hour=detail_config.get("run_deadline_hour", 9),
            run_deadline_tz=detail_config.get("run_deadline_tz", "Europe/Vienna"),
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

    def _run_deduplication_job(self):
        """Run deduplication to remove fuzzy duplicates."""
        logger.info("Starting scheduled deduplication job")
        try:
            stats = self.cleanup_service.deduplicate_programs()
            logger.info(
                f"Deduplication completed: {stats.get('duplicates_removed', 0)} duplicates removed "
                f"from {stats.get('duplicate_groups', 0)} groups"
            )
        except Exception as e:
            logger.error(f"Deduplication job failed: {e}", exc_info=True)

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
            # Run deduplication after full import
            self._run_deduplication_job()
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
            # Cleanup after grid import
            self._run_cleanup_job()
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
        """
        Run detail enrichment for grid-imported Ultimate Backend programs.

        One concurrent task per grid-EPG provider (see
        UltimateBackendDetailEnrichmentService.enrich_all_providers), each
        running until its own backlog is empty or the shared deadline hits.
        """
        if not self.ultimate_enabled or not self.detail_enrichment_service:
            return
        logger.info("Starting Ultimate Backend detail enrichment job")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stats = loop.run_until_complete(
                self.detail_enrichment_service.enrich_all_providers()
            )
            loop.close()
            logger.info(f"Detail enrichment completed: {stats}")
        except Exception as e:
            logger.error(f"Detail enrichment job failed: {e}", exc_info=True)

    def _run_epg_export_job(self):
        """
        Generate compressed EPG exports for all providers nightly.
        """
        from ..services.epg_service import EPGService
        from ..services.provider_service import ProviderService
        import gzip
        import os

        logger.info("Starting nightly EPG export generation")
        epg_service = EPGService()
        provider_service = ProviderService()

        # Read from nested export config
        export_config = self.config.get("export", {})
        export_dir = export_config.get("dir", "/tmp/epg_exports")
        os.makedirs(export_dir, exist_ok=True)

        providers = provider_service.list_providers(enabled_only=True)
        results = []

        for provider in providers:
            try:
                logger.info(f"Generating export for provider: {provider.name}")

                # Get channels and programs
                channels, programs = epg_service.get_provider_programs_for_export(
                    provider_id=provider.id,
                    start_time=None,  # Use defaults (7 days past)
                    end_time=None,    # Use defaults (7 days future)
                )

                if not channels:
                    logger.warning(f"No channels for provider {provider.name}, skipping")
                    continue

                # Serialize to XML
                from ..parsers.xmltv_serializer import XMLTVSerializer
                serializer = XMLTVSerializer()

                xml_output = serializer.serialize_tv(
                    channels=channels,
                    programs=programs,
                    generator_info_name="EPG Service/1.0.0",
                    generator_info_url="https://github.com/your-repo/epg-service",
                    source_info_name=provider.name,
                    source_info_url=provider.xmltv_url if provider.xmltv_url else None,
                )

                # Write compressed file
                filename = f"epg_{provider.name.replace(' ', '_')}.xml.gz"
                filepath = os.path.join(export_dir, filename)

                with gzip.open(filepath, 'wt', encoding='utf-8') as f:
                    f.write(xml_output)

                size_mb = os.path.getsize(filepath) / (1024 * 1024)
                logger.info(f"Exported {provider.name}: {len(programs)} programs, {size_mb:.2f} MB")

                results.append({
                    "provider": provider.name,
                    "programs": len(programs),
                    "channels": len(channels),
                    "size_mb": round(size_mb, 2),
                    "filepath": filepath,
                })

            except Exception as e:
                logger.error(f"Failed to export {provider.name}: {e}", exc_info=True)
                results.append({
                    "provider": provider.name,
                    "error": str(e),
                })

        logger.info(f"Nightly EPG export completed: {len(results)} providers processed")
        return results

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

        # Weekly deduplication (Sunday at 5:00 AM - after cleanup)
        self.scheduler.add_job(
            self._run_deduplication_job,
            trigger=CronTrigger(day_of_week=6, hour=5, minute=0),
            id="weekly_deduplication",
            name="Weekly Deduplication",
            replace_existing=True,
        )
        logger.info("Scheduled weekly deduplication on Sunday at 5:00 AM")

        # Nightly EPG export generation - READ FROM NESTED CONFIG
        # Default pushed to 09:00 (was 05:00) so it runs after the detail
        # enrichment deadline (detail_config.run_deadline_hour, default
        # 09:00) instead of exporting mid-run with partial descriptions.
        export_config = self.config.get("export", {})
        if export_config.get("enabled", True):
            export_time = export_config.get("time", "09:00")
            hour, minute = map(int, export_time.split(":"))

            self.scheduler.add_job(
                self._run_epg_export_job,
                trigger=CronTrigger(hour=hour, minute=minute),
                id="nightly_epg_export",
                name="Nightly EPG Export",
                replace_existing=True,
            )
            logger.info(f"Scheduled nightly EPG export at {export_time}")

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

            # Grid import: midnight Vienna time.
            # Channel-based import is NOT scheduled — it runs as a fallback
            # only when grid_import_all() returns empty for a provider.
            self.scheduler.add_job(
                self._run_grid_import_job,
                trigger=CronTrigger(hour=0, minute=0, timezone="Europe/Vienna"),
                id="daily_grid_import",
                name="Ultimate Backend Grid Import",
                replace_existing=True,
            )
            logger.info("Scheduled daily grid import at midnight Vienna time")

            # Detail enrichment: 1 AM Vienna time (after grid import).
            # Runs one task per grid-EPG provider, each until its backlog is
            # empty or the shared deadline (see detail_config.run_deadline_hour,
            # default 09:00) is reached.
            self.scheduler.add_job(
                self._run_detail_enrichment_job,
                trigger=CronTrigger(hour=1, minute=0, timezone="Europe/Vienna"),
                id="daily_detail_enrichment",
                name="Ultimate Backend Detail Enrichment",
                replace_existing=True,
            )
            logger.info("Scheduled daily detail enrichment at 1:00 AM Vienna time")

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

    def trigger_cleanup_now(self, retention_days: int = None, deduplicate: bool = True):
        """
        Immediately run cleanup outside the daily schedule.

        Args:
            retention_days: Days to keep (default from config)
            deduplicate: Whether to run deduplication
        """
        logger.info("Manually triggering cleanup")
        from ..services.cleanup_service import CleanupService

        if retention_days is None:
            retention_days = self.config.get("retention_days", 7)

        service = CleanupService()
        deleted = service.cleanup_old_programs(retention_days)
        logger.info(f"Manual cleanup deleted {deleted} programs")

        dedup_stats = None
        if deduplicate:
            dedup_stats = service.deduplicate_programs()
            logger.info(f"Manual deduplication removed {dedup_stats.get('duplicates_removed', 0)} duplicates")

        return {"programs_deleted": deleted, "deduplication": dedup_stats}

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