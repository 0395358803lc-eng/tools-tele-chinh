"""Small time helpers shared by persistence and Telegram runtime code."""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now_naive() -> datetime:
    """Return current UTC time as a naive datetime for existing SQLite columns.

    The project intentionally stores naive UTC datetimes today. Using
    datetime.now(timezone.utc) avoids the deprecated naive-UTC constructor while
    preserving the current database representation and avoiding a schema
    migration.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
