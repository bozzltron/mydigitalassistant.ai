"""Natural language to cron expression parsing for scheduled tasks."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import NamedTuple

from croniter import croniter


class ParsedSchedule(NamedTuple):
    cron_expr: str
    human: str
    next_run_utc: datetime


SCHEDULE_TZ = "UTC"


def parse_schedule(text: str) -> ParsedSchedule:
    """Convert natural language to a cron expression and next run time.

    Supports:
      - "every X minutes" / "every X hours" → */X * * * * or appropriate cron
      - "every 30 minutes" → */30 * * * * (max frequency)
      - "hourly" → 0 * * * *
      - "daily" / "every day" → 0 9 * * * (default 9am UTC)
      - "every morning at 9am" → 0 9 * * *
      - "every week" / "weekly" → 0 9 * * 1
      - "monthly" → 0 9 1 * *
      - "every monday at 9am" → 0 9 * * 1
      - "at 9am" → 0 9 * * * (today or tomorrow 9am UTC)

    Returns ParsedSchedule with cron_expr, human description, and next_run_utc.
    Raises ValueError for unparseable input.
    """
    text = text.lower().strip()
    now = datetime.now(tz=UTC)

    normalized = _normalize_text(text)

    # Every X minutes (max 30)
    m = re.match(
        r"every (\d+)\s*(?:minute|minutes|min|mins?)", normalized
    )
    if m:
        minutes = int(m.group(1))
        if minutes < 5:
            raise ValueError(
                f"Minimum interval is 5 minutes, got {minutes}. "
                "Schedules under 30 minutes are normalized to 30 minutes."
            )
        if minutes > 30:
            minutes = 30
        return ParsedSchedule(
            cron_expr=f"*/{minutes} * * * *",
            human=f"every {minutes} minutes",
            next_run_utc=_next_cron(f"*/{minutes} * * * *", now),
        )

    # Every X hours
    m = re.match(
        r"every (\d+)\s*(?:hour|hours|hr|hrs?)", normalized
    )
    if m:
        hours = int(m.group(1))
        if hours > 23:
            raise ValueError(f"Hourly schedules max out at 23 hours, got {hours}.")
        return ParsedSchedule(
            cron_expr=f"0 */{hours} * * *",
            human=f"every {hours} hours",
            next_run_utc=_next_cron(f"0 */{hours} * * *", now),
        )

    # Every half hour / 30 minutes
    if any(
        k in normalized
        for k in ["half hour", "30 minute", "30 min", "every half"]
    ):
        return ParsedSchedule(
            cron_expr="*/30 * * * *",
            human="every 30 minutes",
            next_run_utc=_next_cron("*/30 * * * *", now),
        )

    # Hourly
    if "hourly" in normalized or normalized == "every hour":
        return ParsedSchedule(
            cron_expr="0 * * * *",
            human="hourly",
            next_run_utc=_next_cron("0 * * * *", now),
        )

    # Every [weekday] at HH:MM
    weekday_map = {
        "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
        "friday": 5, "saturday": 6, "sunday": 0,
    }
    for day_name, day_num in weekday_map.items():
        m = re.match(
            rf"every {day_name}at\s*(\d{{1,2}})(?::(\d{{2}}))?\s*(?:am|pm)?",
            normalized,
        )
        if not m:
            m = re.match(
                rf"every {day_name}\s+at\s+(\d{{1,2}})(?::(\d{{2}}))?\s*(?:am|pm)?",
                normalized,
            )
        if m:
            hour, minute = _parse_time(m.group(1), m.group(2))
            return ParsedSchedule(
                cron_expr=f"{minute} {hour} * * {day_num}",
                human=f"every {day_name} at {hour:02d}:{minute:02d} UTC",
                next_run_utc=_next_cron(
                    f"{minute} {hour} * * {day_num}", now
                ),
            )

    # Every morning/evening at specific time
    m = re.match(
        r"every\s+(morning|afternoon|evening|night)\s+at\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        normalized,
    )
    if m:
        part_of_day = m.group(1)
        hour = int(m.group(2))
        minute = int(m.group(3) or "0")
        ampm = m.group(4)
        if ampm:
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
        else:
            hour_map = {
                "morning": 9, "afternoon": 14, "evening": 18, "night": 20
            }
            hour = hour_map.get(part_of_day, hour)
        return ParsedSchedule(
            cron_expr=f"{minute} {hour} * * *",
            human=f"every {part_of_day} at {hour:02d}:{minute:02d} UTC",
            next_run_utc=_next_cron(f"{minute} {hour} * * *", now),
        )

    # Daily at HH:MM
    m = re.match(
        r"(?:daily|every day|everyday)\s*(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        normalized,
    )
    if m:
        hour, minute = _parse_time(m.group(1), m.group(2), m.group(3))
        return ParsedSchedule(
            cron_expr=f"{minute} {hour} * * *",
            human=f"daily at {hour:02d}:{minute:02d} UTC",
            next_run_utc=_next_cron(f"{minute} {hour} * * *", now),
        )

    # At time (today/tomorrow)
    m = re.match(
        r"at\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", normalized
    )
    if m:
        hour, minute = _parse_time(m.group(1), m.group(2), m.group(3))
        return ParsedSchedule(
            cron_expr=f"{minute} {hour} * * *",
            human=f"daily at {hour:02d}:{minute:02d} UTC",
            next_run_utc=_next_cron(f"{minute} {hour} * * *", now),
        )

    # Weekly
    if "weekly" in normalized or "every week" in normalized:
        return ParsedSchedule(
            cron_expr="0 9 * * 1",
            human="weekly on Monday at 9am UTC",
            next_run_utc=_next_cron("0 9 * * 1", now),
        )

    # Monthly
    if "monthly" in normalized or "every month" in normalized:
        return ParsedSchedule(
            cron_expr="0 9 1 * *",
            human="monthly on the 1st at 9am UTC",
            next_run_utc=_next_cron("0 9 1 * *", now),
        )

    # Daily (no time specified)
    if "daily" in normalized or "every day" in normalized:
        return ParsedSchedule(
            cron_expr="0 9 * * *",
            human="daily at 9am UTC",
            next_run_utc=_next_cron("0 9 * * *", now),
        )

    raise ValueError(
        f"Could not parse schedule from '{text}'. "
        "Try phrases like 'every 30 minutes', 'daily at 9am', 'weekly', "
        "'every Monday at 9am', or 'hourly'."
    )


def next_run_from_cron(cron_expr: str, after: datetime | None = None) -> datetime:
    """Return the next UTC datetime this cron expression will fire after `after`."""
    now = after or datetime.now(tz=UTC)
    return _next_cron(cron_expr, now)


def _next_cron(cron_expr: str, after: datetime) -> datetime:
    iter_ = croniter(cron_expr, after, tz=SCHEDULE_TZ)
    return iter_.get_next(datetime)


def _parse_time(
    hour_str: str | None,
    minute_str: str | None,
    ampm: str | None = None,
) -> tuple[int, int]:
    hour = int(hour_str or "0")
    minute = int(minute_str or "0")
    if ampm:
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    return hour, minute


def _normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text
