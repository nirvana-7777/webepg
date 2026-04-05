"""
Background job scheduler for EPG service.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..services.cleanup_service import CleanupService
from ..services.import_service import ImportService
from ..services.ultimate_backend_discovery_service import UltimateBackendDiscoveryService
from ..services.ultimate_backend_import_service import UltimateBackendImportService

logger = logging.getLogger(__name__)


class JobScheduler:
    """Manages background jobs for imports and cleanup."""

    def __init__(self, config: dict):
        """
        Initialize job scheduler.

        Args:
            config: Configuration dictionary with scheduler settings
        """
        self.config = config
        self.scheduler = BackgroundScheduler(timezone=config.get("timezone", "UTC"))
        self.import_service = ImportService()
        self.cleanup_service = CleanupService()

        # Initialize Ultimate Backend components if enabled
        self.ultimate_enabled = config.get("ultimate_backend", {}).get("enabled", False)
        self.ultimate_client = None
        self.ultimate_discovery_service = None
        if self.ultimate_enabled:
            self._init_ultimate_backend(config)

    def _init_ultimate_backend(self, config: dict):
        """Initialize Ultimate Backend components."""
        from ..clients.ultimate_backend_client import UltimateBackendClient

        ub_config = config.get("ultimate_backend", {})
        instance = ub_config.get("instance", {})
        import_config = ub_config.get("import", {})

        self.ultimate_client = UltimateBackendClient(
            base_url=instance.get("base_url", "http://ultimate:7777"),
            api_key=instance.get("api_key"),
            timeout_seconds=import_config.get("timeout_seconds", 30),
            max_retries=import_config.get("max_retries", 3),
            requests_per_second=import_config.get("max_requests_per_second", 5),
        )

        self.ultimate_discovery_service = UltimateBackendDiscoveryService(self.ultimate_client)
        self._ultimate_import_service = UltimateBackendImportService(
            client=self.ultimate_client,
            future_days=import_config.get("future_days", 7),
            past_days=import_config.get("past_days", 7),
            chunk_hours=import_config.get("chunk_hours", 24),
            max_concurrent_channels=import_config.get("max_concurrent_channels", 3),
        )

        logger.info("Ultimate Backend integration initialized")

    def _run_import_job(self):
        """Execute import job for all XMLTV providers."""
        logger.info("Starting scheduled XMLTV import job")

        try:
            logs = self.import_service.import_all_providers()

            success_count = sum(1 for log in logs if log.status == "success")
            failed_count = len(logs) - success_count

            logger.info(
                f"XMLTV import job completed: {success_count} succeeded, "
                f"{failed_count} failed"
            )

            # Run cleanup after imports
            self._run_cleanup_job()

        except Exception as e:
            logger.error(f"XMLTV import job failed: {e}", exc_info=True)

    def _run_cleanup_job(self):
        """Execute cleanup job."""
        logger.info("Starting scheduled cleanup job")

        try:
            retention_days = self.config.get("retention_days", 7)
            deleted_count = self.cleanup_service.cleanup_old_programs(retention_days)

            logger.info(f"Cleanup job completed: {deleted_count} programs deleted")

        except Exception as e:
            logger.error(f"Cleanup job failed: {e}", exc_info=True)

    def _run_ultimate_discovery_job(self):
        """Execute Ultimate Backend discovery job."""
        if not self.ultimate_enabled:
            return

        logger.info("Starting Ultimate Backend discovery job")

        try:
            # Run async discovery
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stats = loop.run_until_complete(self.ultimate_discovery_service.discover_all())
            loop.close()

            logger.info(f"Ultimate Backend discovery completed: {stats}")
        except Exception as e:
            logger.error(f"Ultimate Backend discovery job failed: {e}", exc_info=True)

    def _run_ultimate_import_job(self):
        """Execute Ultimate Backend incremental import job."""
        if not self.ultimate_enabled:
            return

        logger.info("Starting Ultimate Backend incremental import job")

        try:
            # Run async import
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stats = loop.run_until_complete(self.ultimate_import_service.incremental_import_all())
            loop.close()

            logger.info(f"Ultimate Backend import completed: {stats}")

            # Run cleanup after import
            self._run_cleanup_job()

        except Exception as e:
            logger.error(f"Ultimate Backend import job failed: {e}", exc_info=True)

    def start(self):
        """Start the scheduler with configured jobs."""
        # Parse import time (e.g., "03:00")
        import_time = self.config.get("import_time", "03:00")
        hour, minute = map(int, import_time.split(":"))

        # Schedule daily XMLTV import job
        self.scheduler.add_job(
            self._run_import_job,
            trigger=CronTrigger(hour=hour, minute=minute),
            id="daily_import",
            name="Daily XMLTV Import",
            replace_existing=True,
        )
        logger.info(f"Scheduled daily XMLTV import at {import_time}")

        # Schedule Ultimate Backend jobs if enabled
        if self.ultimate_enabled:
            ub_config = self.config.get("ultimate_backend", {})
            discovery_config = ub_config.get("discovery", {})

            # Discovery job (weekly by default)
            if discovery_config.get("enabled", True):
                discovery_day = discovery_config.get("day", 6)  # 6 = Sunday (Monday=0, Sunday=6)
                discovery_hour = discovery_config.get("hour", 2)

                self.scheduler.add_job(
                    self._run_ultimate_discovery_job,
                    trigger=CronTrigger(day_of_week=discovery_day, hour=discovery_hour, minute=0),
                    id="weekly_ultimate_discovery",
                    name="Weekly Ultimate Backend Discovery",
                    replace_existing=True,
                )
                logger.info(f"Scheduled weekly Ultimate Backend discovery on day {discovery_day} at {discovery_hour}:00")

            # Incremental import job (daily, 30 minutes after XMLTV import)
            import_minute = minute + 30
            if import_minute >= 60:
                import_minute = import_minute - 60
                import_hour = hour + 1
            else:
                import_hour = hour

            self.scheduler.add_job(
                self._run_ultimate_import_job,
                trigger=CronTrigger(hour=import_hour, minute=import_minute),
                id="daily_ultimate_import",
                name="Daily Ultimate Backend Import",
                replace_existing=True,
            )
            logger.info(f"Scheduled daily Ultimate Backend import at {import_hour}:{import_minute:02d}")

        # Start scheduler
        self.scheduler.start()
        logger.info("Job scheduler started")

    def stop(self):
        """Stop the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Job scheduler stopped")

            # Close Ultimate Backend client if exists
            if self.ultimate_client:
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.ultimate_client.close())
                    loop.close()
                except Exception as e:
                    logger.warning(f"Error closing Ultimate Backend client: {e}")

    def trigger_import_now(self):
        """Manually trigger XMLTV import job immediately."""
        logger.info("Manually triggering XMLTV import job")

        # Run in a separate job to avoid blocking
        self.scheduler.add_job(
            self._run_import_job,
            id="manual_import",
            name="Manual XMLTV Import",
            replace_existing=True,
        )

    def trigger_ultimate_import_now(self):
        """Manually trigger Ultimate Backend import immediately."""
        if not self.ultimate_enabled:
            logger.warning("Ultimate Backend not enabled, cannot trigger import")
            return

        logger.info("Manually triggering Ultimate Backend import")
        self.scheduler.add_job(
            self._run_ultimate_import_job,
            id="manual_ultimate_import",
            name="Manual Ultimate Backend Import",
            replace_existing=True,
        )

    def trigger_ultimate_discovery_now(self):
        """Manually trigger Ultimate Backend discovery immediately."""
        if not self.ultimate_enabled:
            logger.warning("Ultimate Backend not enabled, cannot trigger discovery")
            return

        logger.info("Manually triggering Ultimate Backend discovery")
        self.scheduler.add_job(
            self._run_ultimate_discovery_job,
            id="manual_ultimate_discovery",
            name="Manual Ultimate Backend Discovery",
            replace_existing=True,
        )

    def get_next_run_time(self, job_id: str = "daily_import") -> Optional[datetime]:
        """
        Get next scheduled run time for a job.

        Args:
            job_id: Job identifier

        Returns:
            Next run datetime or None if job not found
        """
        job = self.scheduler.get_job(job_id)
        if job:
            return job.next_run_time
        return None

    @property
    def ultimate_import_service(self):
        return self._ultimate_import_service if hasattr(self, '_ultimate_import_service') else None