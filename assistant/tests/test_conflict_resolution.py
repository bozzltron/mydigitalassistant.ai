from assistant.backend.memory.confidence import MAX_CONFIDENCE
from assistant.backend.memory.store import MemoryStore


async def test_contradiction_then_auto_and_manual_resolution(store: MemoryStore):
    frame = await store.create_frame("guitar", "entity")

    # Turn 1: initial value.
    slot, _ = await store.upsert_slot(frame.id, "strings", "6")
    assert slot.value == "6"

    # Turn 2: new value wins because confidence is equal -> recency bias.
    slot, conflict = await store.upsert_slot(frame.id, "strings", "12")
    assert slot.value == "12"
    assert conflict is not None
    assert conflict.status == "auto_resolved"

    history = await store.get_slot_history(slot.id)
    resolved = next((h for h in history if h["reason"] == "revise"), None)
    assert resolved is not None
    assert resolved["old_value"] == "6"
    assert resolved["new_value"] == "12"

    turn2_conflicts = await store.get_conflicts_for_frame(frame.id)
    assert any(c.status == "auto_resolved" and c.new_value == "12" for c in turn2_conflicts)

    # Turn 3a: introduce value 8, also wins by recency.
    slot, _ = await store.upsert_slot(frame.id, "strings", "8")
    assert slot.value == "8"

    # Turn 3b: repeat value 8 to push its confidence above a fresh contradiction.
    for _ in range(4):
        slot, _ = await store.upsert_slot(frame.id, "strings", "8")
    assert slot.confidence > 0.5
    assert slot.confidence <= MAX_CONFIDENCE

    # Turn 3c: a fresh contradicting value now loses because 8's confidence is higher.
    # The ladder decides (existing stands) and now records that it decided: this used
    # to be 'pending', which read as "unresolved" when the outcome had been applied.
    slot, decided_conflict = await store.upsert_slot(frame.id, "strings", "12")
    assert slot.value == "8"
    assert decided_conflict is not None
    assert decided_conflict.status == "auto_resolved"
    assert decided_conflict.resolved_value == "8"

    # The user can still override the decision by hand; that escape hatch is
    # independent of how the automatic pass recorded itself.
    resolved_slot = await store.manual_override_conflict(decided_conflict.id, "10")
    assert resolved_slot.value == "10"

    final_conflicts = await store.get_conflicts_for_frame(frame.id)
    override_conflict = next(c for c in final_conflicts if c.id == decided_conflict.id)
    assert override_conflict.status == "manual_override"
    assert override_conflict.resolved_value == "10"

    final_history = await store.get_slot_history(slot.id)
    assert any(h["reason"] == "manual_override" for h in final_history)


async def test_learn_false_then_correct_then_recall(store: MemoryStore):
    """Smoke test: learn wrong fact, correct it, recall truth."""
    frame = await store.create_frame("capybara", "entity")

    slot, _ = await store.upsert_slot(frame.id, "size", "small")
    assert slot.value == "small"

    slot, conflict = await store.upsert_slot(frame.id, "size", "large")
    assert slot.value == "large"
    assert conflict is not None

    await store.manual_override_conflict(conflict.id, "medium")

    corrected_slot = await store.get_slot(frame.id, "size")
    assert corrected_slot.value == "medium"

    history = await store.get_slot_history(corrected_slot.id)
    override_events = [h for h in history if h["reason"] == "manual_override"]
    assert len(override_events) == 1
    assert override_events[0]["old_value"] == "large"
    assert override_events[0]["new_value"] == "medium"
