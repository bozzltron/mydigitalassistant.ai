"""The `.ics` writer: a model-written iCalendar document, or a prose fallback.

The writer used to accept a `WHEN | SUMMARY | LOCATION | END` line convention. It
was removed: the model writes ICS directly (better than the convention could
express — descriptions, statuses, timezones), so the writer validates the document
and uses it, and the line parser was brittle machinery around something the model
already does. The library does the formatting in both shapes.

Regression: a live write stored one event titled "BEGIN:VCALENDAR" with the real
calendar escaped inside its DESCRIPTION, so reading the file back yielded the
wrapper instead of the events. The model had supplied a valid ICS document as
`content`; the writer treated it as prose.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from icalendar import Calendar

from assistant.backend import timeutil
from assistant.backend.pipeline.files import render_file_bytes


def _events(data: bytes):
    return Calendar.from_ical(data).walk("VEVENT")


_MODEL_ICS = (
    "BEGIN:VCALENDAR\n"
    "VERSION:2.0\n"
    "PRODID:-//Why Not Release Calendar//EN\n"
    "BEGIN:VEVENT\n"
    "DTSTART;VALUE=DATE:20261022\n"
    "DTEND;VALUE=DATE:20261023\n"
    "SUMMARY:Why Not - Song Release\n"
    "DESCRIPTION:Official release of the single.\n"
    "LOCATION:Online\n"
    "END:VEVENT\n"
    "BEGIN:VEVENT\n"
    "DTSTART;VALUE=DATE:20261030\n"
    "DTEND;VALUE=DATE:20261031\n"
    "SUMMARY:Why Not - Release Party\n"
    "LOCATION:Austin, TX\n"
    "END:VEVENT\n"
    "END:VCALENDAR\n"
)


def test_full_ics_document_is_used_as_is():
    data, err = render_file_bytes("ics", _MODEL_ICS)
    assert err is None and data is not None
    events = _events(data)
    assert [str(e["SUMMARY"]) for e in events] == [
        "Why Not - Song Release",
        "Why Not - Release Party",
    ]
    # The description survived — the writer does not reinterpret the document.
    assert str(events[0]["DESCRIPTION"]) == "Official release of the single."


def test_full_ics_document_round_trips_through_the_reader():
    from assistant.backend.pipeline.files import extract_text_from_ics

    data, _ = render_file_bytes("ics", _MODEL_ICS)
    text, _, _ = extract_text_from_ics(data)
    assert "Song Release" in text
    assert "Release Party" in text
    assert "BEGIN:VCALENDAR" not in text


def test_timed_document_keeps_its_timezone():
    doc = (
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\n"
        "DTSTART;TZID=America/Chicago:20261012T150000\n"
        "DTEND;TZID=America/Chicago:20261012T160000\n"
        "SUMMARY:Dentist\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    data, err = render_file_bytes("ics", doc)
    assert err is None and data is not None
    assert _events(data)[0]["DTSTART"].params.get("TZID") == "America/Chicago"


def test_ics_marker_without_events_falls_back_to_prose():
    """A stray marker is not a document; the prose path still produces a VEVENT."""
    data, err = render_file_bytes("ics", "BEGIN:VCALENDAR\nnot really ical\n")
    assert err is None and data is not None
    assert len(_events(data)) == 1


def test_prose_falls_back_to_a_single_event_dated_today(monkeypatch):
    fixed = ZoneInfo("America/Chicago")
    monkeypatch.setattr(timeutil, "local_tz", lambda: fixed)
    data, err = render_file_bytes("ics", "Trip to Rainier\nbring boots")
    assert err is None and data is not None
    events = _events(data)
    assert len(events) == 1
    assert str(events[0]["SUMMARY"]) == "Trip to Rainier"
    assert events[0]["DTSTART"].dt == datetime.now(fixed).date()
