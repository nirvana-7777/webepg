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
                    sql, (past_cutoff.isoformat(), future_cutoff.isoformat())
                )
                deleted_count = cursor.rowcount

            logger.info(f"Deleted {deleted_count} programs outside retention window")

            # Also clean up old import logs (keep last 100)
            self._cleanup_old_import_logs()

            return deleted_count

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            raise

    @staticmethod
    def _cleanup_old_import_logs(keep_count: int = 100) -> int:
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

    def deduplicate_programs(self, time_tolerance_minutes: int = 5) -> dict:
        """
        Find and remove fuzzy duplicate programs.

        Args:
            time_tolerance_minutes: Consider programs within this time window as potential duplicates

        Returns:
            Dictionary with deduplication statistics
        """
        db = get_db()

        stats = {"duplicate_groups": 0, "duplicates_removed": 0}

        try:
            # SIMPLER APPROACH: Find duplicates and delete them one by one

            # Step 1: Find all potential duplicate pairs
            find_duplicates_sql = """
                                  SELECT p1.id                                                              as older_id, \
                                         p2.id                                                              as newer_id, \
                                         p1.channel_id, \
                                         p1.title, \
                                         p1.start_time                                                      as time1, \
                                         p2.start_time                                                      as time2, \
                                         ABS(strftime('%s', p1.start_time) - strftime('%s', p2.start_time)) as time_diff, \
                                         p1.created_at                                                      as created1, \
                                         p2.created_at                                                      as created2
                                  FROM programs p1
                                           JOIN programs p2 ON p1.channel_id = p2.channel_id
                                      AND p1.provider_id = p2.provider_id
                                      AND p1.id < p2.id -- Ensure we don't compare the same pair twice
                                      AND ABS(strftime('%s', p1.start_time) - strftime('%s', p2.start_time)) < ?
                                  WHERE (p1.title LIKE '%' || p2.title || '%' OR p2.title LIKE '%' || p1.title || '%')
                                    AND p1.title IS NOT NULL
                                    AND p2.title IS NOT NULL
                                    AND p1.created_at < p2.created_at -- p1 is older than p2
                                  ORDER BY p1.channel_id, p1.start_time, time_diff \
                                  """

            time_tolerance_seconds = time_tolerance_minutes * 60

            duplicate_pairs = db.fetchall(
                find_duplicates_sql, (time_tolerance_seconds,)
            )

            if not duplicate_pairs:
                logger.info("No fuzzy duplicates found")
                return stats

            # Step 2: Collect IDs to delete (always delete the OLDER one)
            ids_to_delete = set()
            processed_pairs = 0

            for row in duplicate_pairs:
                (
                    older_id,
                    newer_id,
                    channel_id,
                    title,
                    time1,
                    time2,
                    time_diff,
                    created1,
                    created2,
                ) = row

                # Add the older ID to deletion list
                ids_to_delete.add(older_id)
                processed_pairs += 1

                logger.debug(
                    f"Marking duplicate for deletion: ID {older_id} (created: {created1}) "
                    f"-> Keeping ID {newer_id} (created: {created2}) | "
                    f"Title: {title} | Time diff: {time_diff}s"
                )

            stats["duplicate_groups"] = processed_pairs

            if not ids_to_delete:
                return stats

            # Step 3: Delete the marked programs
            delete_ids = list(ids_to_delete)

            # Delete in batches to avoid SQL parameter limits
            batch_size = 100
            total_deleted = 0

            for i in range(0, len(delete_ids), batch_size):
                batch = delete_ids[i : i + batch_size]
                placeholders = ",".join(["?"] * len(batch))

                delete_sql = f"""
                DELETE FROM programs
                WHERE id IN ({placeholders})
                """

                with db.get_cursor() as cursor:
                    cursor.execute(delete_sql, batch)
                    deleted_in_batch = cursor.rowcount
                    total_deleted += deleted_in_batch

            stats["duplicates_removed"] = total_deleted

            if total_deleted > 0:
                logger.info(
                    f"Fuzzy deduplication removed {total_deleted} duplicate programs "
                    f"from {processed_pairs} duplicate pairs"
                )

            # Step 4: Also clean up any remaining exact duplicates (same channel, start, end, title)
            # This catches any duplicates that the fuzzy logic might have missed
            exact_duplicates_sql = """
                                   DELETE \
                                   FROM programs
                                   WHERE id NOT IN (SELECT MIN(id) \
                                                    FROM programs \
                                                    GROUP BY channel_id, start_time, end_time, title) \
                                   """

            with db.get_cursor() as cursor:
                cursor.execute(exact_duplicates_sql)
                exact_deleted = cursor.rowcount

            if exact_deleted > 0:
                stats["exact_duplicates_removed"] = exact_deleted
                stats["duplicates_removed"] += exact_deleted
                logger.info(f"Removed {exact_deleted} exact duplicates")

            return stats

        except Exception as e:
            logger.error(f"Error during fuzzy deduplication: {e}", exc_info=True)
            raise

    @staticmethod
    def get_database_stats() -> dict:
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
            stats["total_programs"] = row[0] if row else 0

            # Count channels
            row = db.fetchone("SELECT COUNT(*) FROM channels")
            stats["total_channels"] = row[0] if row else 0

            # Count providers
            row = db.fetchone("SELECT COUNT(*) FROM providers")
            stats["total_providers"] = row[0] if row else 0

            # Get date range of programs
            row = db.fetchone("SELECT MIN(start_time), MAX(start_time) FROM programs")
            if row and row[0]:
                stats["earliest_program"] = row[0]
                stats["latest_program"] = row[1]

            # Get last import time
            row = db.fetchone("""
                SELECT MAX(completed_at)
                FROM import_log
                WHERE status = 'success'
                """)
            if row and row[0]:
                stats["last_successful_import"] = row[0]

            logger.debug(f"Database stats: {stats}")

            return stats

        except Exception as e:
            logger.error(f"Error getting database stats: {e}")
            raise
