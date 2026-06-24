"""
Admin endpoints for statistics, duplicates, and import status.
"""

import logging
from flask import Blueprint, jsonify, request

from . import ServiceRegistry

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/import/trigger", methods=["POST"])
def trigger_import():
    """Manually trigger import for all providers."""
    try:
        scheduler = ServiceRegistry.scheduler
        assert scheduler is not None, "Scheduler not initialized"
        scheduler.trigger_import_now()

        next_run = scheduler.get_next_run_time()
        return jsonify(
            {
                "message": "Import job triggered",
                "next_scheduled_import": (next_run.isoformat() if next_run else None),
            }
        )

    except Exception as e:
        logger.error(f"Error triggering import: {e}")
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/import/status", methods=["GET"])
def import_status():
    """Get import status and next scheduled run time."""
    try:
        scheduler = ServiceRegistry.scheduler
        assert scheduler is not None, "Scheduler not initialized"
        from ..database.models import ImportLog
        from ..database.connection import get_db

        db = get_db()

        # Get recent import logs
        rows = db.fetchall("""
            SELECT id, provider_id, started_at, completed_at, status,
                   programs_imported, programs_skipped, error_message
            FROM import_log
            ORDER BY started_at DESC
            LIMIT 10
            """)

        logs = [ImportLog.from_db_row(row).to_dict() for row in rows]

        next_run = scheduler.get_next_run_time()

        return jsonify(
            {
                "next_scheduled_import": next_run.isoformat() if next_run else None,
                "recent_imports": logs,
            }
        )

    except Exception as e:
        logger.error(f"Error getting import status: {e}")
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/admin/duplicates", methods=["DELETE"])
def remove_duplicates():
    """
    Remove duplicate programs from the database.

    This operation finds programs with the same channel, start time, end time, and title,
    and removes all but the newest version of each duplicate.
    """
    try:
        from ..services.cleanup_service import CleanupService

        cleanup_service = CleanupService()
        stats = cleanup_service.deduplicate_programs()

        return (
            jsonify(
                {
                    "success": True,
                    "message": "Duplicate programs removed successfully",
                    "stats": stats,
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error removing duplicates: {e}")
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/admin/duplicates/preview", methods=["GET"])
def preview_duplicates():
    """
    Preview fuzzy duplicate programs without removing them.
    """
    try:
        from ..database.connection import get_db

        db = get_db()

        # Get time tolerance from query parameter (default 5 minutes)
        time_tolerance = request.args.get("time_tolerance", default=5, type=int)

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
                                                                             CASE \
                                                                                 WHEN p1.title LIKE \
                                                                                      '%' || p2.title || '%' OR \
                                                                                      p2.title LIKE \
                                                                                      '%' || p1.title || '%' THEN 1.0 \
                                                                                 WHEN p1.title LIKE p2.title || '%' OR p2.title LIKE p1.title || '%' \
                                                                                     THEN 0.9 \
                                                                                 ELSE 0.0 \
                                                                                 END                                                            as title_similarity \
                                                                      FROM programs p1 \
                                                                               JOIN programs p2 \
                                                                                    ON p1.channel_id = p2.channel_id \
                                                                                        AND \
                                                                                       p1.provider_id = p2.provider_id \
                                                                                        AND p1.id < p2.id \
                                                                                        AND \
                                                                                       ABS(strftime('%s', p1.start_time) - strftime('%s', p2.start_time)) < \
                                                                                       ? \
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
                                        WHERE title_similarity >= 0.7 -- Lower threshold for preview
                                        ORDER BY channel_id, start_time, time_diff_seconds LIMIT 50 \
                                        """

        time_tolerance_seconds = time_tolerance * 60

        potential_dups = db.fetchall(
            find_potential_duplicates_sql, (time_tolerance_seconds,)
        )

        duplicate_list = []
        seen_pairs = set()

        for row in potential_dups:
            (
                id1,
                id2,
                channel_id,
                title1,
                title2,
                start1,
                start2,
                time_diff,
                similarity,
                created1,
                created2,
            ) = row

            # Avoid duplicates in the list
            pair_key = f"{min(id1, id2)}_{max(id1, id2)}"
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            # Get channel info
            channel_sql = "SELECT name, display_name FROM channels WHERE id = ?"
            channel_row = db.fetchone(channel_sql, (channel_id,))

            duplicate_list.append(
                {
                    "programs": [
                        {
                            "id": id1,
                            "title": title1,
                            "start_time": start1 + "Z",
                            "created_at": created1 + "Z" if created1 else None,
                        },
                        {
                            "id": id2,
                            "title": title2,
                            "start_time": start2 + "Z",
                            "created_at": created2 + "Z" if created2 else None,
                        },
                    ],
                    "channel": {
                        "id": channel_id,
                        "name": channel_row[0] if channel_row else None,
                        "display_name": channel_row[1] if channel_row else None,
                    },
                    "match_quality": {
                        "time_difference_seconds": time_diff,
                        "title_similarity": similarity,
                        "would_be_removed": (
                            created1 < created2 if created1 and created2 else False
                        ),
                    },
                }
            )

        # Get estimated removal count
        estimated_removals = (
            sum(1 for dup in duplicate_list if dup["match_quality"]["would_be_removed"])
            if duplicate_list
            else 0
        )

        return jsonify(
            {
                "preview": True,
                "message": "Fuzzy duplicate programs preview",
                "time_tolerance_minutes": time_tolerance,
                "examples": duplicate_list,
                "estimated_removal_count": estimated_removals,
                "total_examples_found": len(duplicate_list),
            }
        )

    except Exception as e:
        logger.error(f"Error previewing duplicates: {e}")
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/statistics", methods=["GET"])
def get_statistics():
    """Get comprehensive statistics about the EPG database."""
    try:
        from ..database.connection import get_db

        db = get_db()
        stats = {}

        # Basic counts
        row = db.fetchone("SELECT COUNT(*) FROM channels")
        stats["total_channels"] = row[0] if row else 0

        row = db.fetchone("SELECT COUNT(*) FROM programs")
        stats["total_programs"] = row[0] if row else 0

        row = db.fetchone("SELECT COUNT(*) FROM providers")
        stats["total_providers"] = row[0] if row else 0

        row = db.fetchone("SELECT COUNT(*) FROM channel_aliases")
        stats["total_aliases"] = row[0] if row else 0

        # Date ranges
        row = db.fetchone("""
                          SELECT MIN(start_time)                   as earliest,
                                 MAX(start_time)                   as latest,
                                 COUNT(DISTINCT DATE (start_time)) as days_covered
                          FROM programs
                          """)
        if row and row[0]:
            stats["earliest_program"] = row[0]
            stats["latest_program"] = row[1]
            stats["days_covered"] = row[2]

        # Programs per day (last 7 days)
        rows = db.fetchall("""
                           SELECT
                               DATE (start_time) as date, COUNT (*) as count
                           FROM programs
                           WHERE start_time > datetime('now', '-7 days')
                           GROUP BY DATE (start_time)
                           ORDER BY date DESC
                           """)
        stats["programs_last_7_days"] = [{"date": r[0], "count": r[1]} for r in rows]

        # Import statistics
        row = db.fetchone("""
                          SELECT COUNT(*)                                            as total_imports,
                                 SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_imports,
                                 SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)  as failed_imports,
                                 MAX(completed_at)                                   as last_import
                          FROM import_log
                          """)
        if row:
            stats["imports_total"] = row[0] or 0
            stats["imports_successful"] = row[1] or 0
            stats["imports_failed"] = row[2] or 0
            stats["last_import"] = row[3]

        return jsonify(stats)

    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        return jsonify({"error": str(e)}), 500