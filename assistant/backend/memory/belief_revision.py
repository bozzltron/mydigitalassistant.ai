"""AGM belief revision — formal expand/revise/contract operators for slot memory.

Implements the Alchourrón-Gärdenfors-Makinson (AGM) belief revision postulates
for slot-level belief change. Each slot value is treated as an independent
propositional belief; the belief set for a frame is the set of all accepted
slot values.

Operations:
- expand(frame_id, key, value):  add a new belief without removing anything.
  When the new value matches the existing, confidence bumps (repeat reinforcement).
- revise(frame_id, key, new_value):  add a new belief, contract the old one if
  they conflict. This is the standard conflict-resolution path in upsert_slot.
- contract(frame_id, key, value):  remove a specific belief. Called when a
  user correction identifies a specific wrong value (e.g. "it's not 6, it's 12").

Entrenchment ordering (which beliefs to give up when contracting):
  Higher source_reliability  → more entrenched
  Higher confidence         → more entrenched (within same reliability)

The six AGM postulates guide the implementation:
  (⊥)    Success:     revise(φ) ⊢ φ  (new belief is accepted)
  (⊥)    Inclusion:   revise(φ) ⊆ K ⊕ {φ}  (revision ⊆ expansion)
  (⊥)    Vacuity:     ¬(φ → ψ) ∈ K → K ⋆ ψ = K ⊕ ψ  (no conflict → no change)
  (⊥)    Consistency: If φ is consistent, so is K ⋆ φ
  (⊥)    Extensionality:  φ ≡ ψ implies K ⋆ φ = K ⋆ ψ
  (⊥)    Conjunction:  K ⋆ φ ⋆ ψ ⊆ (K ⋆ φ) ⋆ ψ

Reference: Alchourrón, Gärdenfors & Makinson (1985). "On the Logic of Theory
Change: Partial Meet Contraction and Revision Functions."
"""

from dataclasses import dataclass
from enum import StrEnum

from assistant.backend.memory.confidence import (
    ConflictResolution,
    initial_confidence,
    resolve_conflict,
)


class OperationType(StrEnum):
    """Type of belief-change operation recorded in slot_history."""
    EXPAND = "expand"           # new belief added, no removal
    REVISE = "revise"          # new belief added, old one contracted
    CONTRACT = "contract"      # specific belief removed
    MANUAL_OVERRIDE = "manual_override"  # user explicitly resolved a conflict
    INITIAL = "initial"        # first value inserted (no conflict possible)


@dataclass
class RevisionResult:
    """Result of an AGM belief revision operation on a slot."""
    operation: OperationType
    old_value: str | None       # value that was removed (if any)
    new_value: str              # value that was accepted
    conflict: bool               # whether a conflict was detected and resolved
    resolution: ConflictResolution | None  # NEW_WINS, EXISTING_WINS, MERGE


def expand(
    existing_slot: dict | None,
    new_value: str,
    source_type: str | None,
    source_reliability: float | None,
    priority: float,
) -> RevisionResult:
    """AGM expand: add a new belief without removing anything.

    If the new value equals the existing value → confidence bump (repeat).
    If different → no-op (must use revise to change).
    """
    if existing_slot is None:
        return RevisionResult(
            operation=OperationType.INITIAL,
            old_value=None,
            new_value=new_value,
            conflict=False,
            resolution=None,
        )

    existing_value = existing_slot["value"]
    if existing_value == new_value:
        return RevisionResult(
            operation=OperationType.EXPAND,
            old_value=None,   # no removal
            new_value=new_value,
            conflict=False,
            resolution=None,
        )

    return RevisionResult(
        operation=OperationType.EXPAND,
        old_value=None,
        new_value=new_value,
        conflict=False,
        resolution=None,
    )


def revise(
    existing_slot: dict | None,
    new_value: str,
    source_type: str | None,
    new_source_reliability: float | None,
    new_priority: float,
) -> RevisionResult:
    """AGM revise: add a new belief, contract the old one if they conflict.

    This is the standard conflict-resolution path. Uses the reliability-weighted
    resolve_conflict decision to choose between old and new values.

    Returns the operation type and whether the old value was overwritten.
    """
    if existing_slot is None:
        return RevisionResult(
            operation=OperationType.INITIAL,
            old_value=None,
            new_value=new_value,
            conflict=False,
            resolution=None,
        )

    existing_value = existing_slot["value"]
    existing_confidence = existing_slot.get("confidence") or initial_confidence()
    existing_rel = existing_slot.get("source_reliability") or 0.5

    if existing_value == new_value:
        return RevisionResult(
            operation=OperationType.EXPAND,
            old_value=None,
            new_value=new_value,
            conflict=False,
            resolution=None,
        )

    decision = resolve_conflict(
        existing_value=existing_value,
        new_value=new_value,
        existing_confidence=existing_confidence,
        new_confidence=initial_confidence(),
        existing_source_reliability=existing_rel,
        new_source_reliability=new_source_reliability,
        existing_priority=existing_slot.get("priority", 0.5),
        new_priority=new_priority,
    )

    return RevisionResult(
        operation=OperationType.REVISE,
        old_value=decision.losing_value,
        new_value=decision.winning_value,
        conflict=True,
        resolution=decision.resolution,
    )


def contract(
    existing_slot: dict | None,
    value_to_remove: str,
) -> RevisionResult:
    """AGM contract: remove a specific belief.

    Called when a user explicitly says a specific value is wrong.
    If the value doesn't exist in the slot, this is a no-op (vacuity).
    """
    if existing_slot is None:
        return RevisionResult(
            operation=OperationType.CONTRACT,
            old_value=None,
            new_value="",
            conflict=False,
            resolution=None,
        )

    existing_value = existing_slot["value"]
    if existing_value != value_to_remove:
        return RevisionResult(
            operation=OperationType.CONTRACT,
            old_value=None,   # vacuity: value not present
            new_value=existing_value,
            conflict=False,
            resolution=None,
        )

    return RevisionResult(
        operation=OperationType.CONTRACT,
        old_value=value_to_remove,
        new_value="",
        conflict=False,
        resolution=None,
    )


def should_revise(existing_slot: dict | None, new_value: str) -> bool:
    """Return True if revising with new_value would change the slot.

    This is used by callers that want to know whether a revise operation
    would actually cause a change before calling revise().
    """
    if existing_slot is None:
        return True
    return existing_slot["value"] != new_value
