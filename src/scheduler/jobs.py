"""
Background job scheduler for EPG service.
"""
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..services.import_service import ImportService
from ..services.cleanup_service import CleanupService

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
        self.scheduler = BackgroundScheduler(timezone=config.get('timezone', 'UTC'))
        self.import_service = ImportService()
        self.cleanup_service = CleanupService()

    def _run_import_job(self):
        """Execute import job for all providers."""
        logger.info("Starting scheduled import job")

        try:
            logs = self.import_service.import_all_providers()

            success_count = sum(1 for log in logs if log.status == 'success')
            failed_count = len(logs) - success_count

            logger.info(
                f"Import job completed: {success_count} succeeded, "
                f"{failed_count} failed"
            )

            # Run cleanup after imports
            self._run_cleanup_job()

        except Exception as e:
            logger.error(f"Import job failed: {e}", exc_info=True)

    def _run_cleanup_job(self):
        """Execute cleanup job."""
        logger.info("Starting scheduled cleanup job")

        try:
            retention_days = self.config.get('retention_days', 7)
            deleted_count = self.cleanup_service.cleanup_old_programs(retention_days)

            logger.info(f"Cleanup job completed: {deleted_count} programs deleted")

        except Exception as e:
            logger.error(f"Cleanup job failed: {e}", exc_info=True)

    def start(self):
        """Start the scheduler with configured jobs."""
        # Parse import time (e.g., "03:00")
        import_time = self.config.get('import_time', '03:00')
        hour, minute = map(int, import_time.split(':'))

        # Schedule daily import job
        self.scheduler.add_job(
            self._run_import_job,
            trigger=CronTrigger(hour=hour, minute=minute),
            id='daily_import',
            name='Daily XMLTV Import',
            replace_existing=True
        )

        logger.info(f"Scheduled daily import at {import_time}")

        # Start scheduler
        self.scheduler.start()
        logger.info("Job scheduler started")

    def stop(self):
        """Stop the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Job scheduler stopped")

    def trigger_import_now(self):
        """Manually trigger import job immediately."""
        logger.info("Manually triggering import job")

        # Run in a separate job to avoid blocking
        self.scheduler.add_job(
            self._run_import_job,
            id='manual_import',
            name='Manual Import',
            replace_existing=True
        )

    def get_next_run_time(self, job_id: str = 'daily_import') -> datetime:
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