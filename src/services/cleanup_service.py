"""
Cleanup service for managing data retention.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..database.connection import get_db

logger = logging.getLogger(__name__)

_UTC_MIN = datetime.min.replace(tzinfo=timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime]:
    """
    Parse a stored timestamp into an aware UTC datetime.

    Handles the formats found in the database ("2026-11-18 23:30:46",
    "2026-11-18T23:30:46.711966Z", "...+00:00"). Naive values are assumed to
    be UTC. Unparseable values return None; callers must never delete rows
    they cannot compare.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text[-1] in ("Z", "z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _norm_text(value: Any) -> str:
    """Case/whitespace-insensitive text for matching."""
    if value is None:
        return ""
    return " ".join(str(value).split()).casefold()


def _rank(row: Any) -> tuple:
    """Survivor ranking: newest created_at wins, ties broken by higher id."""
    return (_parse_ts(row["created_at"]) or _UTC_MIN, row["id"])


def _find_duplicate_clusters(
    rows: List[Any], window: timedelta
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Find fuzzy duplicate clusters among the programs of ONE channel.

    Two rows are the same airing when ALL of these hold:
      - same provider_id (never merge across sources)
      - same normalised title and subtitle (exact match, not substring)
      - start times within `window` of the cluster's first start
      - their time ranges overlap (back-to-back airings are distinct)
      - season_num / episode_num do not conflict. An empty value on either
        side is a wildcard (one snapshot may lack episode metadata); two
        different non-empty values keep the rows apart.

    Rows with an empty title or an unparseable start_time are never matched
    (and therefore never deleted).

    Returns:
        (clusters, skipped) where each cluster is
        {"keep": row, "remove": [row, ...]} for clusters with 2+ members,
        and `skipped` counts rows that could not be considered.
    """
    groups: Dict[tuple, List[Tuple[datetime, Optional[datetime], Any]]] = {}
    skipped = 0

    for row in rows:
        title = _norm_text(row["title"])
        start = _parse_ts(row["start_time"])
        if not title or start is None:
            skipped += 1
            continue
        key = (
            row["channel_id"],
            row["provider_id"],
            title,
            _norm_text(row["subtitle"]),
        )
        groups.setdefault(key, []).append((start, _parse_ts(row["end_time"]), row))

    result: List[Dict[str, Any]] = []

    for entries in groups.values():
        if len(entries) < 2:
            continue

        entries.sort(key=lambda e: (e[0], e[2]["id"]))

        clusters: List[Dict[str, Any]] = []
        for start, end, row in entries:
            season = _norm_text(row["season_num"])
            episode = _norm_text(row["episode_num"])

            target: Optional[Dict[str, Any]] = None
            for c in reversed(clusters):
                if start - c["anchor"] > window:
                    break  # anchors ascend; all earlier ones are further away
                if c["end"] is not None and start >= c["end"]:
                    continue  # no overlap -> distinct (e.g. back-to-back) airing
                if c["season"] and season and c["season"] != season:
                    continue
                if c["episode"] and episode and c["episode"] != episode:
                    continue
                target = c
                break

            if target is None:
                clusters.append(
                    {
                        "anchor": start,
                        "end": end,
                        "season": season,
                        "episode": episode,
                        "rows": [row],
                    }
                )
            else:
                target["rows"].append(row)
                if end is not None and (target["end"] is None or end > target["end"]):
                    target["end"] = end
                target["season"] = target["season"] or season
                target["episode"] = target["episode"] or episode

        for c in clusters:
            if len(c["rows"]) > 1:
                keep = max(c["rows"], key=_rank)
                result.append(
                    {
                        "keep": keep,
                        "remove": [r for r in c["rows"] if r["id"] != keep["id"]],
                    }
                )

    return result, skipped


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

        now = datetime.now(timezone.utc)
        past_cutoff = now - timedelta(days=retention_days)
        future_cutoff = now + timedelta(days=retention_days)

        logger.info(
            f"Cleaning up programs outside window: "
            f"{past_cutoff.isoformat()} to {future_cutoff.isoformat()}"
        )

        # datetime() normalises mixed stored formats (space/'T' separator,
        # 'Z' / offset suffix) so the comparison is chronological, not textual.
        sql = """
              DELETE
              FROM programs
              WHERE datetime(start_time) < datetime(?)
                 OR datetime(start_time) > datetime(?)
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
              DELETE
              FROM import_log
              WHERE id NOT IN (SELECT id
                               FROM import_log
                               ORDER BY started_at DESC
                  LIMIT ?
                  )
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

    def deduplicate_programs(
        self,
        time_tolerance_minutes: int = 5,
        dry_run: bool = False,
        force: bool = False,
        max_delete_fraction: float = 0.10,
    ) -> dict:
        """
        Find and remove fuzzy duplicate programs, keeping the newest snapshot.

        The matching rules are documented on _find_duplicate_clusters(). The
        work is done channel by channel in Python (O(n log n) per channel)
        instead of a SQL self-join, which was quadratic and compared
        timestamps as text.

        Args:
            time_tolerance_minutes: Max start-time difference between snapshots
            dry_run: Report what would be removed; delete nothing
            force: Skip the safety limit below
            max_delete_fraction: Abort (delete nothing) if more than this
                fraction of all scanned rows would be deleted. A healthy
                daily run removes a tiny fraction; a huge number means the
                matching is wrong for some data. The first run after a long
                gap may legitimately exceed it: inspect a dry run, then force.

        Returns:
            Dictionary with deduplication statistics. Keys: duplicate_groups,
            duplicates_removed, rows_scanned, rows_skipped, dry_run, and
            would_remove_count / would_remove_ids / would_remove_ids_truncated /
            examples (the id list and examples are display-capped; the count is
            not; all present in dry-run mode when duplicates exist). If the safety limit trips: aborted=True and
            abort_reason.
        """
        if time_tolerance_minutes < 0:
            raise ValueError("time_tolerance_minutes must be >= 0")

        db = get_db()
        window = timedelta(minutes=time_tolerance_minutes)

        stats: Dict[str, Any] = {
            "duplicate_groups": 0,
            "duplicates_removed": 0,
            "rows_scanned": 0,
            "rows_skipped": 0,
            "dry_run": dry_run,
        }

        select_sql = """
            SELECT id, channel_id, provider_id, title, subtitle,
                   season_num, episode_num, start_time, end_time, created_at
            FROM programs
            WHERE channel_id = ?
        """

        try:
            channel_rows = db.fetchall("SELECT DISTINCT channel_id FROM programs")

            all_clusters: List[Dict[str, Any]] = []
            for channel_row in channel_rows:
                rows = db.fetchall(select_sql, (channel_row[0],))
                stats["rows_scanned"] += len(rows)
                clusters, skipped = _find_duplicate_clusters(rows, window)
                stats["rows_skipped"] += skipped
                all_clusters.extend(clusters)

            ids_to_delete = sorted(
                {r["id"] for c in all_clusters for r in c["remove"]}
            )
            stats["duplicate_groups"] = len(all_clusters)
            stats["would_remove_count"] = len(ids_to_delete)

            for c in all_clusters:
                for r in c["remove"]:
                    logger.debug(
                        f"Duplicate: removing ID {r['id']} (created {r['created_at']}), "
                        f"keeping ID {c['keep']['id']} (created {c['keep']['created_at']}) "
                        f"| channel {r['channel_id']} | {r['title']} | start {r['start_time']}"
                    )

            if not ids_to_delete:
                logger.info("No fuzzy duplicates found")
                return stats

            if dry_run:
                # Display cap only: would_remove_count above is the full count.
                stats["would_remove_ids"] = ids_to_delete[:100]
                stats["would_remove_ids_truncated"] = len(ids_to_delete) > 100
                stats["examples"] = [
                    {
                        "channel_id": c["keep"]["channel_id"],
                        "title": c["keep"]["title"],
                        "start_time": c["keep"]["start_time"],
                        "kept": {
                            "id": c["keep"]["id"],
                            "start_time": c["keep"]["start_time"],
                            "created_at": c["keep"]["created_at"],
                        },
                        "removed": [
                            {
                                "id": r["id"],
                                "start_time": r["start_time"],
                                "created_at": r["created_at"],
                            }
                            for r in c["remove"]
                        ],
                    }
                    for c in all_clusters[:20]
                ]
                logger.info(
                    f"DRY RUN: would remove {len(ids_to_delete)} duplicate programs "
                    f"from {len(all_clusters)} groups"
                )
                return stats

            fraction = len(ids_to_delete) / max(stats["rows_scanned"], 1)
            if not force and fraction > max_delete_fraction:
                stats["aborted"] = True
                stats["abort_reason"] = (
                    f"Would delete {len(ids_to_delete)} of {stats['rows_scanned']} "
                    f"programs ({fraction:.1%}), above the {max_delete_fraction:.0%} "
                    f"safety limit. Nothing was deleted."
                )
                logger.error(stats["abort_reason"])
                return stats

            # Delete in batches to stay below SQLite's variable limit
            batch_size = 500
            total_deleted = 0
            for i in range(0, len(ids_to_delete), batch_size):
                batch = ids_to_delete[i : i + batch_size]
                placeholders = ",".join(["?"] * len(batch))
                with db.get_cursor() as cursor:
                    cursor.execute(
                        f"DELETE FROM programs WHERE id IN ({placeholders})", batch
                    )
                    total_deleted += cursor.rowcount

            stats["duplicates_removed"] = total_deleted
            logger.info(
                f"Fuzzy deduplication removed {total_deleted} duplicate programs "
                f"from {len(all_clusters)} groups"
            )
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