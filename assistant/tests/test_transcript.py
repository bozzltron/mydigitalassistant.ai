"""The conversation transcript: a view over episodes, downloadable as text.

`format_transcript` is pure, so it is tested without a store. It is what a
downloaded conversation contains, so the contract is pinned here: a header, one
labelled block per turn, and assistant footers stripped (the loop's scaffolding
is not conversation).
"""

from __future__ import annotations

from assistant.backend.memory.models import Episode
from assistant.backend.pipeline.transcript import (
    format_transcript,
    strip_response_footers,
)


def _ep(role: str, content: str, ts: str | None = "2026-10-04 09:12:00") -> Episode:
    return Episode(user_id=1, session_id="s", role=role, content=content, timestamp=ts)


def test_header_carries_title_export_time_and_turn_count():
    text = format_transcript(
        [_ep("user", "hi"), _ep("assistant", "hello")],
        title="Album planning",
        exported_at="2026-10-06 14:00 (America/Chicago)",
    )
    assert text.startswith("Conversation: Album planning\n")
    assert "Exported: 2026-10-06 14:00 (America/Chicago)" in text
    assert "Turns: 2" in text


def test_each_turn_is_labelled_with_role_and_timestamp():
    text = format_transcript([_ep("user", "what is the plan?", "2026-10-04 09:12:00")])
    assert "[2026-10-04 09:12:00] user:" in text
    assert "what is the plan?" in text


def test_assistant_sources_footer_is_stripped():
    reply = "Here you go.\n\n**Sources:**\n- https://example.com"
    text = format_transcript([_ep("assistant", reply)])
    assert "Here you go." in text
    assert "Sources" not in text
    assert "example.com" not in text


def test_memory_marker_footer_is_stripped():
    reply = "Answer.\n\n<small>_(Answered from memory — 3 frames)_</small>"
    text = format_transcript([_ep("assistant", reply)])
    assert "Answered from memory" not in text
    assert text.rstrip().endswith("Answer.")


def test_user_text_is_not_footer_stripped():
    """Only assistant turns carry footers; a user's literal text is preserved."""
    text = format_transcript([_ep("user", "**Sources:** are my job")])
    assert "**Sources:** are my job" in text


def test_format_is_deterministic():
    eps = [_ep("user", "a"), _ep("assistant", "b")]
    assert format_transcript(eps) == format_transcript(eps)


def test_missing_timestamp_omits_the_stamp():
    text = format_transcript([_ep("user", "no clock", ts=None)])
    assert "\nuser:\nno clock" in text


def test_strip_response_footers_leaves_plain_text_alone():
    assert strip_response_footers("Just an answer.") == "Just an answer."
