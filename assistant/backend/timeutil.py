"""The one place that answers "what time is it, for this user".

The container runs UTC; the user does not. Everything that reports a date or time
to a human -- the system prompt, the scheduler, file writers -- resolves the zone
here, so "today" means the same thing everywhere.

Zone resolution order: ``DAILY_TASKS_TZ`` (explicit) > ``TZ`` env > host local.
An invalid name falls back rather than raising: a misconfigured zone should
degrade to a wrong-but-working clock, not break every turn.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo

from assistant.backend.config import settings

logger = logging.getLogger(__name__)


def _zone(name: str) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        logger.warning("%r is not a valid IANA timezone; falling back", name)
        return None


def local_tz() -> tzinfo:
    """Resolve the user's timezone: DAILY_TASKS_TZ > TZ env > host local."""
    return (
        _zone(settings.daily_tasks_tz.strip())
        or _zone(os.environ.get("TZ", "").strip())
        or datetime.now().astimezone().tzinfo
        or UTC
    )


def current_datetime_str(now: datetime | None = None) -> str:
    """One compact, human line in the user's zone.

    e.g. ``Monday 2026-10-05 14:32 (America/Chicago, UTC-05:00)``. The format is
    fixed-length within a session (a minute rollover does not change its length),
    which is what lets the prompt budget count on it.
    """
    tz = local_tz()
    dt = (now or datetime.now(tz=UTC)).astimezone(tz)
    raw = dt.strftime("%z")  # "+0000" / "-0500"
    offset = f"UTC{raw[:3]}:{raw[3:]}" if len(raw) == 5 else "UTC"
    zone = getattr(tz, "key", None) or dt.tzname() or "local"
    return f"{dt.strftime('%A %Y-%m-%d %H:%M')} ({zone}, {offset})"
