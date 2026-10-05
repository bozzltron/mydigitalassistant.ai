"""The agent must know the date and time, in the user's zone.

The model has no clock; without this it answers "what's today's date?" from its
training cutoff. The date/time is injected into the system prompt (volatile-last)
rather than exposed as a tool, so it costs no round-trip.

The correctness that matters is the *zone*: the container runs UTC, the user does
not. A US user asking at 19:00 local must not be told it is tomorrow.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from assistant.backend import timeutil
from assistant.backend.config import settings
from assistant.backend.pipeline.llm_client import (
    build_system_prompt,
    system_prompt_overhead,
)


def test_current_datetime_uses_the_configured_zone(monkeypatch):
    """DAILY_TASKS_TZ wins, and the offset reflects it — not the container's UTC."""
    monkeypatch.setattr(settings, "daily_tasks_tz", "America/New_York")
    # 2026-10-05 18:32 UTC is 14:32 EDT (UTC-04:00) — same day, four hours back.
    s = timeutil.current_datetime_str(now=datetime(2026, 10, 5, 18, 32, tzinfo=UTC))
    assert s == "Monday 2026-10-05 14:32 (America/New_York, UTC-04:00)"


def test_current_datetime_can_cross_the_date_line(monkeypatch):
    """Late-evening UTC is already *tomorrow* for a positive-offset zone."""
    monkeypatch.setattr(settings, "daily_tasks_tz", "Pacific/Kiritimati")  # UTC+14
    # 2026-10-05 12:00 UTC -> 2026-10-06 02:00 in UTC+14.
    s = timeutil.current_datetime_str(now=datetime(2026, 10, 5, 12, 0, tzinfo=UTC))
    assert "2026-10-06" in s
    assert "UTC+14:00" in s


def test_local_tz_falls_back_from_an_invalid_zone(monkeypatch):
    """A misconfigured zone degrades to a working clock, not a broken turn."""
    monkeypatch.setattr(settings, "daily_tasks_tz", "Not/AZone")
    monkeypatch.setenv("TZ", "America/Chicago")
    assert str(timeutil.local_tz()) == "America/Chicago"


def test_system_prompt_injects_the_datetime_before_memory():
    prompt = build_system_prompt(
        "MEM",
        "functional",
        current_datetime="Monday 2026-10-05 14:32 (America/Chicago, UTC-05:00)",
    )
    assert "Current date and time: Monday 2026-10-05 14:32 (America/Chicago, UTC-05:00)" in prompt
    # Volatile-last: the clock must sit after the stable prefix and before memory
    # so it does not invalidate the cached prefix.
    assert prompt.index("Current date and time:") < prompt.index("relevant memory state")


def test_system_prompt_omits_the_line_when_blank():
    prompt = build_system_prompt("MEM", "functional", current_datetime="")
    assert "Current date and time" not in prompt


def test_system_prompt_computes_the_line_by_default(monkeypatch):
    """The production default (no argument) computes it from the clock."""
    monkeypatch.setattr(
        "assistant.backend.pipeline.llm_client.current_datetime_str",
        lambda: "PINNED",
    )
    prompt = build_system_prompt("MEM", "functional")
    assert "Current date and time: PINNED" in prompt


def test_prompt_overhead_accounts_for_the_datetime_line(monkeypatch):
    """The memory budget must reserve the line, or memory overruns into it."""
    monkeypatch.setattr(
        "assistant.backend.pipeline.llm_client.current_datetime_str",
        lambda: "Monday 2026-10-05 14:32 (America/Chicago, UTC-05:00)",
    )
    with_line = system_prompt_overhead("functional")
    without_line = len(build_system_prompt("", "functional", current_datetime=""))
    assert with_line > without_line


async def test_ics_dtstart_uses_the_configured_zone(monkeypatch):
    """The .ics writer stamps the user's date, not the container's UTC date."""
    from icalendar import Calendar

    from assistant.backend.pipeline.files import render_file_bytes

    fixed = timezone(timedelta(hours=14))
    monkeypatch.setattr(timeutil, "local_tz", lambda: fixed)

    data, err = render_file_bytes("ics", "Trip to Rainier")
    assert err is None and data is not None
    cal = Calendar.from_ical(data)
    event = cal.walk("VEVENT")[0]
    expected = datetime.now(fixed).date()
    assert event["DTSTART"].dt == expected


@pytest.mark.parametrize("ext", ["txt", "md"])
def test_text_writers_unaffected(ext):
    from assistant.backend.pipeline.files import render_file_bytes

    data, err = render_file_bytes(ext, "hello")
    assert err is None and data == b"hello"


async def test_datetime_reaches_the_streaming_path(store, stub_llm, stub_search):
    """The production path is `chat_stream`; the line must be in its system prompt.

    The frontend uses `/chat/stream`, so this — not `chat()` — is the path that
    runs for a user. The injection is shared through `_assemble_prompt`, but this
    pins it at the surface that matters rather than trusting the sharing.
    """
    from assistant.backend.memory.retrieval import Retriever
    from assistant.backend.pipeline.orchestrator import (
        ChatRequest,
        Orchestrator,
        OrchestratorDeps,
    )

    orch = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store=store, llm_client=stub_llm),
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )
    user = await store.create_user("stream_dt_user")
    async for _ in orch.chat_stream(
        ChatRequest(user_id=user.id, message="hello", session_id="stream_dt")
    ):
        pass

    assert any(
        "Current date and time:" in prompt for prompt in stub_llm.system_prompts
    ), "the streaming path did not inject the current date/time"

