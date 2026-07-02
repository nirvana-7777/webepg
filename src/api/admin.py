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


@admin_bp.route("/statistics/providers", methods=["GET"])
def get_provider_statistics():
    """
    Get detailed per-provider statistics including program counts,
    date ranges, and enrichment coverage.
    """
    try:
        from ..database.connection import get_db
        from ..services.provider_service import ProviderService

        db = get_db()
        provider_service = ProviderService()
        providers = provider_service.list_providers()

        result = {
            "total_providers": len(providers),
            "providers": []
        }

        for provider in providers:
            # Get provider-level stats - FIX: join on provider_id too
            row = db.fetchone("""
                SELECT 
                    COUNT(DISTINCT c.id) as channel_count,
                    COUNT(DISTINCT p.id) as program_count,
                    MIN(p.start_time) as earliest_program,
                    MAX(p.start_time) as latest_program,
                    COUNT(DISTINCT DATE(p.start_time)) as days_covered,
                    -- Enrichment stats (programs with description, cast, etc.)
                    COUNT(CASE WHEN p.description IS NOT NULL AND p.description != '' THEN 1 END) as with_description,
                    COUNT(CASE WHEN p.actors IS NOT NULL AND p.actors != '[]' THEN 1 END) as with_actors,
                    COUNT(CASE WHEN p.directors IS NOT NULL AND p.directors != '[]' THEN 1 END) as with_directors,
                    COUNT(CASE WHEN p.category IS NOT NULL AND p.category != '' THEN 1 END) as with_category,
                    COUNT(CASE WHEN p.rating IS NOT NULL AND p.rating != '' THEN 1 END) as with_rating,
                    COUNT(CASE WHEN p.ultimate_epg_id IS NOT NULL THEN 1 END) as with_ultimate_epg_id
                FROM providers pr
                LEFT JOIN channels c ON c.provider_id = pr.id
                LEFT JOIN programs p ON p.channel_id = c.id AND p.provider_id = pr.id
                WHERE pr.id = ?
            """, (provider.id,))

            if row and row[0] is not None:  # provider has channels
                channel_count = row[0] or 0
                program_count = row[1] or 0

                # Calculate enrichment percentages
                enrichment = {
                    "with_description": row[5] or 0,
                    "with_actors": row[6] or 0,
                    "with_directors": row[7] or 0,
                    "with_category": row[8] or 0,
                    "with_rating": row[9] or 0,
                    "with_ultimate_epg_id": row[10] or 0,
                }

                if program_count > 0:
                    enrichment["description_percent"] = round((enrichment["with_description"] / program_count) * 100, 1)
                    enrichment["actors_percent"] = round((enrichment["with_actors"] / program_count) * 100, 1)
                    enrichment["directors_percent"] = round((enrichment["with_directors"] / program_count) * 100, 1)
                    enrichment["category_percent"] = round((enrichment["with_category"] / program_count) * 100, 1)
                    enrichment["rating_percent"] = round((enrichment["with_rating"] / program_count) * 100, 1)
                else:
                    enrichment["description_percent"] = 0.0
                    enrichment["actors_percent"] = 0.0
                    enrichment["directors_percent"] = 0.0
                    enrichment["category_percent"] = 0.0
                    enrichment["rating_percent"] = 0.0

                # Get last import status
                import_row = db.fetchone("""
                    SELECT status, completed_at, programs_imported
                    FROM import_log
                    WHERE provider_id = ?
                    ORDER BY started_at DESC
                    LIMIT 1
                """, (provider.id,))

                provider_stats = {
                    "provider_id": provider.id,
                    "name": provider.name,
                    "display_name": provider.display_name,
                    "source_type": provider.source_type,
                    "enabled": provider.enabled,
                    "channels": channel_count,
                    "programs": program_count,
                    "days_covered": row[4] or 0,
                    "date_range": {
                        "earliest": row[2],
                        "latest": row[3],
                    } if row[2] and row[3] else None,
                    "enrichment": enrichment,
                    "last_import": {
                        "status": import_row[0] if import_row else None,
                        "completed_at": import_row[1] if import_row else None,
                        "programs_imported": import_row[2] if import_row else 0,
                    } if import_row else None,
                }
            else:
                # Provider has no channels or programs yet
                provider_stats = {
                    "provider_id": provider.id,
                    "name": provider.name,
                    "display_name": provider.display_name,
                    "source_type": provider.source_type,
                    "enabled": provider.enabled,
                    "channels": 0,
                    "programs": 0,
                    "days_covered": 0,
                    "date_range": None,
                    "enrichment": {
                        "with_description": 0,
                        "with_actors": 0,
                        "with_directors": 0,
                        "with_category": 0,
                        "with_rating": 0,
                        "with_ultimate_epg_id": 0,
                        "description_percent": 0.0,
                        "actors_percent": 0.0,
                        "directors_percent": 0.0,
                        "category_percent": 0.0,
                        "rating_percent": 0.0,
                    },
                    "last_import": None,
                }

            result["providers"].append(provider_stats)

        # Add summary statistics
        total_programs = sum(p["programs"] for p in result["providers"])
        total_with_description = sum(p["enrichment"]["with_description"] for p in result["providers"])

        result["summary"] = {
            "total_programs": total_programs,
            "total_channels": sum(p["channels"] for p in result["providers"]),
            "programs_with_description": total_with_description,
            "description_coverage_percent": round((total_with_description / total_programs) * 100, 1) if total_programs > 0 else 0.0,
        }

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error getting provider statistics: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/admin/cleanup", methods=["POST"])
def trigger_cleanup():
    """
    Manually trigger cleanup of old programs and deduplication.

    Query params:
        retention_days: int - Number of days to keep (default from config)
        deduplicate: bool - Whether to run deduplication (default: True)
        past_only: bool - Only clean past data, don't limit future (default: False)
    """
    try:
        from ..services.cleanup_service import CleanupService
        from ..config import load_config

        # Get parameters
        retention_days = request.args.get("retention_days", type=int)
        deduplicate = request.args.get("deduplicate", default=True, type=bool)
        past_only = request.args.get("past_only", default=False, type=bool)

        # Load config for default retention
        if retention_days is None:
            config = load_config()
            retention_days = config.get("retention", {}).get("days", 7)

        cleanup_service = CleanupService()
        results = {
            "retention_days": retention_days,
            "past_only": past_only,
            "deduplicate": deduplicate,
        }

        # Run retention cleanup
        if past_only:
            # FIX: Use timezone-aware datetime and remove unused future_cutoff
            from datetime import datetime, timedelta, timezone
            from ..database.connection import get_db
            now = datetime.now(timezone.utc)
            past_cutoff = now - timedelta(days=retention_days)

            db = get_db()
            sql = "DELETE FROM programs WHERE start_time < ?"
            with db.get_cursor() as cursor:
                cursor.execute(sql, (past_cutoff.isoformat(),))
                deleted_count = cursor.rowcount
            results["programs_deleted"] = deleted_count
            logger.info(f"Past-only cleanup deleted {deleted_count} programs older than {retention_days} days")
        else:
            # Normal cleanup (keeps past AND future window)
            deleted_count = cleanup_service.cleanup_old_programs(retention_days)
            results["programs_deleted"] = deleted_count

        # Run deduplication if requested
        dedup_stats = None
        if deduplicate:
            dedup_stats = cleanup_service.deduplicate_programs()
            results["deduplication"] = dedup_stats

        # Get database stats after cleanup
        stats = cleanup_service.get_database_stats()
        results["database_after"] = stats

        return jsonify({
            "success": True,
            "message": "Cleanup completed successfully",
            "results": results,
        })

    except Exception as e:
        logger.error(f"Error during cleanup: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/admin/cleanup/status", methods=["GET"])
def get_cleanup_status():
    """Get current cleanup status and database health."""
    try:
        from ..services.cleanup_service import CleanupService
        import os

        cleanup_service = CleanupService()
        stats = cleanup_service.get_database_stats()

        # Get database file size
        from ..database.connection import get_db
        db = get_db()
        try:
            size_bytes = os.path.getsize(db.db_path)
            size_mb = round(size_bytes / (1024 * 1024), 2)
            size_gb = round(size_bytes / (1024 * 1024 * 1024), 2)
        except Exception:
            size_mb = 0
            size_gb = 0

        # Check for potential issues
        warnings = []
        if stats.get("total_programs", 0) > 500000:
            warnings.append("Database has >500k programs - consider running cleanup")
        if size_mb > 500:
            warnings.append(f"Database is {size_mb}MB - consider VACUUM or archiving")

        return jsonify({
            "database_stats": stats,
            "database_size_mb": size_mb,
            "database_size_gb": size_gb,
            "warnings": warnings,
            "status": "healthy" if not warnings else "needs_attention",
        })

    except Exception as e:
        logger.error(f"Error getting cleanup status: {e}")
        return jsonify({"error": str(e)}), 500