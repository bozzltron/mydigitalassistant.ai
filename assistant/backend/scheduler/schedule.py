"""The daily clock for scheduled tasks (redesigned Phase 7).

One wake-up time per day, configured via DAILY_TASKS_TIME (default 09:00) in
DAILY_TASKS_TZ (default: TZ env var or host-local zone). Every enabled "daily"
task fires at that tick; "once" tasks fire at the next tick and disable
themselves. No cron expressions anywhere.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, tzinfo

from assistant.backend.config import settings
from assistant.backend.timeutil import local_tz


def _tz() -> tzinfo:
    """Resolve the configured timezone.

    Delegates to the shared resolver so the scheduler and the system prompt agree
    on the zone. See ``assistant.backend.timeutil``.
    """
    return local_tz()


def parse_daily_time(value: str) -> tuple[int, int]:
    """Parse "HH:MM" (24h). Raises ValueError otherwise."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
    if not m:
        raise ValueError(f"DAILY_TASKS_TIME must be HH:MM (24h), got {value!r}")
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        raise ValueError(f"DAILY_TASKS_TIME out of range: {value!r}")
    return hour, minute


def next_daily_run(now: datetime | None = None) -> datetime:
    """Next occurrence of the daily tick, timezone-aware."""
    hour, minute = parse_daily_time(settings.daily_tasks_time)
    tz = _tz()
    local_now = (now or datetime.now(tz=UTC)).astimezone(tz)
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate


def format_next_run(dt: datetime) -> str:
    """Human phrasing of the next run in the configured zone ("Saturday at 09:00")."""
    local = dt.astimezone(_tz())
    return local.strftime("%A at %H:%M")
