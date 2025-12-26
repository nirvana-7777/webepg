"""
Cleanup service for managing data retention.
"""
import logging
from datetime import datetime, timedelta

from ..database.connection import get_db

logger = logging.getLogger(__name__)


class CleanupService:
    """Service for cleaning up old EPG data."""

    def cleanup_old_programs(self, retention_days: int) -> int:
        """
        Delete programs outside the retention window.

        Keeps programs from (now - retention_days) to (now + retention_days).

        Args:
            retention_days: Number of days to retain in past and future

        Returns:
            Number of programs deleted
        """
        db = get_db()

        now = datetime.utcnow()
        past_cutoff = now - timedelta(days=retention_days)
        future_cutoff = now + timedelta(days=retention_days)

        logger.info(
            f"Cleaning up programs outside window: "
            f"{past_cutoff.isoformat()} to {future_cutoff.isoformat()}"
        )

        sql = """
              DELETE \
              FROM programs
              WHERE start_time < ? \
                 OR start_time > ? \
              """

        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    sql,
                    (past_cutoff.isoformat(), future_cutoff.isoformat())
                )
                deleted_count = cursor.rowcount

            logger.info(f"Deleted {deleted_count} programs outside retention window")

            # Also clean up old import logs (keep last 100)
            self._cleanup_old_import_logs()

            return deleted_count

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            raise

    def _cleanup_old_import_logs(self, keep_count: int = 100) -> int:
        """
        Delete old import log entries, keeping the most recent ones.

        Args:
            keep_count: Number of recent logs to keep

        Returns:
            Number of logs deleted
        """
        db = get_db()

        sql = """
              DELETE \
              FROM import_log
              WHERE id NOT IN (SELECT id \
                               FROM import_log \
                               ORDER BY started_at DESC
                  LIMIT ?
                  ) \
              """

        try:
            with db.get_cursor() as cursor:
                cursor.execute(sql, (keep_count,))
                deleted_count = cursor.rowcount

            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} old import log entries")

            return deleted_count

        except Exception as e:
            logger.error(f"Error cleaning up import logs: {e}")
            raise

    def get_database_stats(self) -> dict:
        """
        Get statistics about the database contents.

        Returns:
            Dictionary with database statistics
        """
        db = get_db()

        stats = {}

        try:
            # Count programs
            row = db.fetchone("SELECT COUNT(*) FROM programs")
            stats['total_programs'] = row[0] if row else 0

            # Count channels
            row = db.fetchone("SELECT COUNT(*) FROM channels")
            stats['total_channels'] = row[0] if row else 0

            # Count providers
            row = db.fetchone("SELECT COUNT(*) FROM providers")
            stats['total_providers'] = row[0] if row else 0

            # Get date range of programs
            row = db.fetchone(
                "SELECT MIN(start_time), MAX(start_time) FROM programs"
            )
            if row and row[0]:
                stats['earliest_program'] = row[0]
                stats['latest_program'] = row[1]

            # Get last import time
            row = db.fetchone(
                """
                SELECT MAX(completed_at)
                FROM import_log
                WHERE status = 'success'
                """
            )
            if row and row[0]:
                stats['last_successful_import'] = row[0]

            logger.debug(f"Database stats: {stats}")

            return stats

        except Exception as e:
            logger.error(f"Error getting database stats: {e}")
            raise