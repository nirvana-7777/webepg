# file: src/utils/time_utils.py
"""
Utility functions for consistent UTC time handling.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


def to_utc_isoformat(dt: Optional[datetime]) -> Optional[str]:
    """
    Convert datetime to ISO 8601 string with 'Z' suffix.

    Args:
        dt: Datetime object (naive or aware)

    Returns:
        ISO 8601 string with 'Z' suffix (e.g., "2026-01-13T14:00:00Z")
    """
    if dt is None:
        return None

    # Ensure datetime is aware and in UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Format with 'Z' suffix
    return dt.isoformat().replace("+00:00", "Z")


def now_utc() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


def next_local_hour_utc(hour: int, tz_name: str) -> datetime:
    """Next occurrence of ``hour``:00 in ``tz_name``, as an aware UTC datetime."""
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    target = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc)
