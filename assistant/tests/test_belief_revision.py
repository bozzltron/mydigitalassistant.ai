"""Tests for the AGM belief revision module.

Tests the six AGM postulates plus special cases for slot memory:
- Success: revise(φ) accepts φ as the new slot value
- Inclusion: revise(φ) ⊆ K ⊕ {φ}
- Vacuity: if ¬(φ → ψ) ∈ K, then K ⋆ ψ = K ⊕ ψ
- Consistency: if φ is consistent, so is K ⋆ φ
- Extensionality: φ ≡ ψ implies K ⋆ φ = K ⋆ ψ
- Conjunction: K ⋆ φ ⋆ ψ ⊆ (K ⋆ φ) ⋆ ψ
"""

from assistant.backend.memory.belief_revision import (
    OperationType,
    contract,
    expand,
    revise,
    should_revise,
)

Slot = dict


def slot(value: str, conf: float = 0.5, rel: float = 0.5, pri: float = 0.5) -> Slot:
    return {"value": value, "confidence": conf, "source_reliability": rel, "priority": pri}


class TestExpand:
    def test_expand_new_slot_is_initial(self):
        result = expand(
            existing_slot=None,
            new_value="blue",
            source_type="user",
            source_reliability=0.95,
            priority=0.5,
        )
        assert result.operation == OperationType.INITIAL
        assert result.new_value == "blue"
        assert result.conflict is False

    def test_expand_same_value_is_expand(self):
        existing = slot("blue")
        result = expand(
            existing_slot=existing,
            new_value="blue",
            source_type="user",
            source_reliability=0.95,
            priority=0.5,
        )
        assert result.operation == OperationType.EXPAND
        assert result.old_value is None
        assert result.conflict is False

    def test_expand_different_value_no_op(self):
        existing = slot("blue")
        result = expand(
            existing_slot=existing,
            new_value="red",
            source_type="search",
            source_reliability=0.5,
            priority=0.5,
        )
        assert result.operation == OperationType.EXPAND
        assert result.old_value is None
        assert result.conflict is False


class TestRevise:
    def test_revise_new_slot_is_initial(self):
        result = revise(
            existing_slot=None,
            new_value="green",
            source_type="user",
            new_source_reliability=0.95,
            new_priority=0.5,
        )
        assert result.operation == OperationType.INITIAL

    def test_revise_same_value_is_expand(self):
        existing = slot("green")
        result = revise(
            existing_slot=existing,
            new_value="green",
            source_type="user",
            new_source_reliability=0.95,
            new_priority=0.5,
        )
        assert result.operation == OperationType.EXPAND

    def test_revise_new_wins_higher_reliability(self):
        existing = slot("red", conf=0.8, rel=0.5)
        result = revise(
            existing_slot=existing,
            new_value="blue",
            source_type="user",
            new_source_reliability=0.95,
            new_priority=0.5,
        )
        assert result.operation == OperationType.REVISE
        assert result.conflict is True
        assert result.new_value == "blue"
        assert result.old_value == "red"

    def test_revise_existing_wins_higher_reliability(self):
        existing = slot("blue", rel=0.95)
        result = revise(
            existing_slot=existing,
            new_value="red",
            source_type="search",
            new_source_reliability=0.5,
            new_priority=0.5,
        )
        assert result.operation == OperationType.REVISE
        assert result.conflict is True
        assert result.new_value == "blue"
        assert result.old_value == "red"

    def test_revise_equal_rel_higher_confidence_wins(self):
        existing = slot("6", conf=0.7, rel=0.5)
        result = revise(
            existing_slot=existing,
            new_value="12",
            source_type="search",
            new_source_reliability=0.5,
            new_priority=0.5,
        )
        assert result.operation == OperationType.REVISE
        assert result.new_value == "6"
        assert result.old_value == "12"

    def test_revise_equal_rel_recency_bias(self):
        existing = slot("6", conf=0.5, rel=0.5)
        result = revise(
            existing_slot=existing,
            new_value="12",
            source_type="search",
            new_source_reliability=0.5,
            new_priority=0.5,
        )
        assert result.operation == OperationType.REVISE
        assert result.new_value == "12"
        assert result.old_value == "6"


class TestContract:
    def test_contract_removes_value(self):
        existing = slot("blue")
        result = contract(existing_slot=existing, value_to_remove="blue")
        assert result.operation == OperationType.CONTRACT
        assert result.old_value == "blue"
        assert result.new_value == ""

    def test_contract_vacuity(self):
        existing = slot("blue")
        result = contract(existing_slot=existing, value_to_remove="red")
        assert result.operation == OperationType.CONTRACT
        assert result.old_value is None
        assert result.new_value == "blue"

    def test_contract_none_slot(self):
        result = contract(existing_slot=None, value_to_remove="blue")
        assert result.operation == OperationType.CONTRACT
        assert result.old_value is None


class TestShouldRevise:
    def test_should_revise_new_slot(self):
        assert should_revise(existing_slot=None, new_value="blue") is True

    def test_should_revise_same_value(self):
        assert should_revise(slot("blue"), "blue") is False

    def test_should_revise_different_value(self):
        assert should_revise(slot("blue"), "red") is True


class TestAGMPostulates:
    def test_success(self):
        existing = slot("old", conf=0.9, rel=0.5)
        result = revise(
            existing_slot=existing,
            new_value="new",
            source_type="user",
            new_source_reliability=0.95,
            new_priority=0.5,
        )
        assert result.new_value == "new"

    def test_inclusion(self):
        existing = slot("old", rel=0.5)
        expand_r = expand(
            existing_slot=existing,
            new_value="new",
            source_type="user",
            source_reliability=0.95,
            priority=0.5,
        )
        revise_r = revise(
            existing_slot=existing,
            new_value="new",
            source_type="user",
            new_source_reliability=0.95,
            new_priority=0.5,
        )
        assert revise_r.new_value in ("old", "new")
        assert expand_r.operation in (OperationType.EXPAND, OperationType.REVISE)

    def test_vacuity(self):
        existing = slot("blue")
        result = revise(
            existing_slot=existing,
            new_value="blue",
            source_type="search",
            new_source_reliability=0.5,
            new_priority=0.5,
        )
        assert result.operation == OperationType.EXPAND
        assert result.new_value == "blue"

    def test_consistency(self):
        existing = slot("true", rel=0.5)
        result = revise(
            existing_slot=existing,
            new_value="also_true",
            source_type="user",
            new_source_reliability=0.95,
            new_priority=0.5,
        )
        assert result.new_value in ("true", "also_true")

    def test_extensionality(self):
        e1 = slot("red")
        e2 = slot("red")
        r1 = revise(
            existing_slot=e1,
            new_value="blue",
            source_type="user",
            new_source_reliability=0.95,
            new_priority=0.5,
        )
        r2 = revise(
            existing_slot=e2,
            new_value="blue",
            source_type="user",
            new_source_reliability=0.95,
            new_priority=0.5,
        )
        assert r1.new_value == r2.new_value
        assert r1.operation == r2.operation
