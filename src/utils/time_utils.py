# file: src/utils/time_utils.py
"""
Utility functions for consistent UTC time handling.
"""

from datetime import datetime, timezone
from typing import Optional


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
