import pytest

from assistant.backend.memory.confidence import (
    MAX_CONFIDENCE,
    bump_confidence,
    initial_confidence,
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
