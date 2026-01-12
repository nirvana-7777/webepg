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

    def deduplicate_programs(self, time_tolerance_minutes: int = 5, title_similarity_threshold: float = 0.8) -> dict:
        """
        Find and remove fuzzy duplicate programs.

        Args:
            time_tolerance_minutes: Consider programs within this time window as potential duplicates
            title_similarity_threshold: Minimum similarity ratio for titles (0.0 to 1.0)

        Returns:
            Dictionary with deduplication statistics
        """
        db = get_db()

        stats = {
            "duplicate_groups": 0,
            "duplicates_removed": 0,
            "fuzzy_matches_considered": True
        }

        try:
            # First, let's find potential duplicates using fuzzy matching
            # This query finds programs that are likely the same show
            find_potential_duplicates_sql = """
                                            WITH potential_duplicates AS (SELECT p1.id                                                              as id1, \
                                                                                 p2.id                                                              as id2, \
                                                                                 p1.channel_id, \
                                                                                 p1.title                                                           as title1, \
                                                                                 p2.title                                                           as title2, \
                                                                                 p1.start_time, \
                                                                                 p1.end_time, \
                                                                                 p2.start_time                                                      as start_time2, \
                                                                                 p2.end_time                                                        as end_time2, \
                                                                                 p1.created_at                                                      as created1, \
                                                                                 p2.created_at                                                      as created2, \
                                                                                 ABS(strftime('%s', p1.start_time) - strftime('%s', p2.start_time)) as time_diff_seconds, \
                                                                                 -- Simple title similarity (check if one title contains the other) \
                                                                                 CASE \
                                                                                     WHEN p1.title LIKE \
                                                                                          '%' || p2.title || '%' OR \
                                                                                          p2.title LIKE \
                                                                                          '%' || p1.title || '%' \
                                                                                         THEN 1.0 \
                                                                                     WHEN p1.title LIKE p2.title || '%' OR p2.title LIKE p1.title || '%' \
                                                                                         THEN 0.9 \
                                                                                     ELSE 0.0 \
                                                                                     END                                                            as title_similarity \
                                                                          FROM programs p1 \
                                                                                   JOIN programs p2 \
                                                                                        ON p1.channel_id = p2.channel_id \
                                                                                            AND p1.provider_id = \
                                                                                                p2.provider_id \
                                                                                            AND p1.id < \
                                                                                                p2.id -- Avoid comparing same program twice \
                                                                                            AND \
                                                                                           ABS(strftime('%s', p1.start_time) - strftime('%s', p2.start_time)) < \
                                                                                           ? -- Within time tolerance \
                                                                          WHERE p1.title IS NOT NULL \
                                                                            AND p2.title IS NOT NULL)
                                            SELECT id1, \
                                                   id2, \
                                                   channel_id, \
                                                   title1, \
                                                   title2, \
                                                   start_time, \
                                                   start_time2, \
                                                   time_diff_seconds, \
                                                   title_similarity, \
                                                   created1, \
                                                   created2
                                            FROM potential_duplicates
                                            WHERE title_similarity >= ?
                                            ORDER BY channel_id, start_time, time_diff_seconds \
                                            """

            time_tolerance_seconds = time_tolerance_minutes * 60

            potential_dups = db.fetchall(
                find_potential_duplicates_sql,
                (time_tolerance_seconds, title_similarity_threshold)
            )

            if not potential_dups:
                logger.info("No fuzzy duplicates found")
                return stats

            # Group duplicates by channel and approximate time
            duplicate_groups = {}
            for row in potential_dups:
                id1, id2, channel_id, title1, title2, start1, start2, time_diff, similarity, created1, created2 = row

                # Create a group key based on channel and time window
                time_key = f"{channel_id}_{start1[:13]}"  # Channel + hour precision

                if time_key not in duplicate_groups:
                    duplicate_groups[time_key] = {
                        "channel_id": channel_id,
                        "program_ids": set(),
                        "titles": set(),
                        "created_times": {}
                    }

                duplicate_groups[time_key]["program_ids"].add(id1)
                duplicate_groups[time_key]["program_ids"].add(id2)
                duplicate_groups[time_key]["titles"].add(title1)
                duplicate_groups[time_key]["titles"].add(title2)
                duplicate_groups[time_key]["created_times"][id1] = created1
                duplicate_groups[time_key]["created_times"][id2] = created2

            stats["duplicate_groups"] = len(duplicate_groups)
            total_to_remove = 0

            # For each duplicate group, keep only the newest program
            for group_key, group_data in duplicate_groups.items():
                program_ids = list(group_data["program_ids"])

                if len(program_ids) <= 1:
                    continue

                # Find the newest program (most recent created_at)
                newest_id = None
                newest_time = None

                for prog_id in program_ids:
                    created_time = group_data["created_times"].get(prog_id)
                    if created_time and (newest_time is None or created_time > newest_time):
                        newest_time = created_time
                        newest_id = prog_id

                if newest_id is None:
                    # No timestamps, keep first one
                    newest_id = program_ids[0]

                # Remove all except the newest
                ids_to_remove = [pid for pid in program_ids if pid != newest_id]

                if ids_to_remove:
                    # Delete the older duplicates
                    placeholders = ','.join(['?'] * len(ids_to_remove))
                    delete_sql = f"DELETE FROM programs WHERE id IN ({placeholders})"

                    with db.get_cursor() as cursor:
                        cursor.execute(delete_sql, ids_to_remove)
                        removed = cursor.rowcount

                    total_to_remove += removed

                    logger.debug(
                        f"Removed {removed} duplicates from channel {group_data['channel_id']}. "
                        f"Kept program {newest_id} (newest). Titles: {', '.join(group_data['titles'])}"
                    )

            stats["duplicates_removed"] = total_to_remove

            if total_to_remove > 0:
                logger.info(
                    f"Fuzzy deduplication removed {total_to_remove} duplicates from "
                    f"{len(duplicate_groups)} duplicate groups"
                )

            return stats

        except Exception as e:
            logger.error(f"Error during fuzzy deduplication: {e}")
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
