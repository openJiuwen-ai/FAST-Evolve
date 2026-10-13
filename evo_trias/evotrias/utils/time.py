from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with offset, the format used in every persisted record."""
    return datetime.now(UTC).isoformat()
