import pytest

from assistant.backend.memory.confidence import (
    MAX_CONFIDENCE,
    bump_confidence,
    bump_priority,
    forget_priority,
    initial_confidence,
    initial_priority,
    max_priority,
    resolve_conflict,
)


def test_bump_confidence_increases():
    assert bump_confidence(0.5) > 0.5


def test_bump_confidence_bounded():
    assert bump_confidence(MAX_CONFIDENCE) == MAX_CONFIDENCE
    assert bump_confidence(0.986) == MAX_CONFIDENCE


def test_bump_confidence_from_zero():
    assert bump_confidence(0.0) == pytest.approx(0.3)


def test_initial_confidence():
    assert initial_confidence() == 0.5


def test_resolve_conflict_new_wins_when_higher():
    decision = resolve_conflict("old", "new", 0.5, 0.9)
    assert decision.winning_value == "new"
    assert decision.losing_value == "old"


def test_resolve_conflict_existing_wins_when_higher():
    decision = resolve_conflict("old", "new", 0.9, 0.5)
    assert decision.winning_value == "old"
    assert decision.losing_value == "new"


def test_resolve_conflict_recency_bias_when_equal():
    decision = resolve_conflict("old", "new", 0.5, 0.5)
    assert decision.winning_value == "new"
    assert decision.losing_value == "old"


def test_resolve_conflict_explicit_none_confidence():
    decision = resolve_conflict("old", "new", 0.9, None)
    assert decision.winning_value == "old"
    assert decision.losing_value == "new"
    assert "existing_confidence" in decision.reason


def test_initial_priority():
    assert initial_priority() == 0.5


def test_bump_priority_increases():
    assert bump_priority(0.5) == 0.7


def test_bump_priority_bounded():
    assert bump_priority(0.9) == 1.0
    assert bump_priority(1.0) == 1.0


def test_max_priority():
    assert max_priority() == 1.0


def test_forget_priority():
    assert forget_priority() == 0.0


def test_resolve_conflict_source_reliability_wins_over_confidence():
    """source_reliability takes precedence over confidence in conflict resolution."""
    decision = resolve_conflict(
        "old",
        "new",
        existing_confidence=0.9,
        new_confidence=0.9,
        existing_source_reliability=0.8,
        new_source_reliability=1.0,
    )
    assert decision.winning_value == "new"
    assert decision.losing_value == "old"
    assert "source_reliability" in decision.reason


def test_resolve_conflict_source_reliability_existing_wins():
    """Higher source_reliability on existing value wins despite lower confidence."""
    decision = resolve_conflict(
        "old",
        "new",
        existing_confidence=0.3,
        new_confidence=0.9,
        existing_source_reliability=1.0,
        new_source_reliability=0.5,
    )
    assert decision.winning_value == "old"
    assert decision.losing_value == "new"


def test_resolve_conflict_equal_reliability_falls_back_to_confidence():
    """When source_reliability is equal, confidence determines winner."""
    decision = resolve_conflict(
        "old",
        "new",
        existing_confidence=0.5,
        new_confidence=0.8,
        existing_source_reliability=0.7,
        new_source_reliability=0.7,
    )
    assert decision.winning_value == "new"
    assert decision.losing_value == "old"
    assert "confidence" in decision.reason


def test_resolve_conflict_equal_reliability_equal_confidence_favors_new():
    """When reliability and confidence are equal, recency bias gives new the win."""
    decision = resolve_conflict(
        "old",
        "new",
        existing_confidence=0.5,
        new_confidence=0.5,
        existing_source_reliability=0.5,
        new_source_reliability=0.5,
    )
    assert decision.winning_value == "new"
    assert decision.losing_value == "old"
    assert "recency" in decision.reason.lower()


def test_resolve_conflict_priority_wins_over_recency():
    """Priority wins over recency when reliability and confidence are equal."""
    decision = resolve_conflict(
        "old",
        "new",
        existing_confidence=0.5,
        new_confidence=0.5,
        existing_source_reliability=0.5,
        new_source_reliability=0.5,
        existing_priority=0.8,
        new_priority=0.3,
    )
    assert decision.winning_value == "old"
    assert decision.losing_value == "new"
