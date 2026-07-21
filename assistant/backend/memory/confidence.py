from dataclasses import dataclass
from enum import Enum

INITIAL_CONFIDENCE = 0.5
REPEAT_DISCOUNT = 0.7  # new_conf = 1 - (1 - old_conf) * 0.7
MAX_CONFIDENCE = 0.99


class ConflictResolution(Enum):
    NEW_WINS = "new_wins"
    EXISTING_WINS = "existing_wins"
    MERGE = "merge"


def bump_confidence(current: float) -> float:
    """Repeated same value: confidence increases, bounded."""
    new_conf = 1 - (1 - current) * REPEAT_DISCOUNT
    return min(new_conf, MAX_CONFIDENCE)


def initial_confidence() -> float:
    return INITIAL_CONFIDENCE


@dataclass
class ConflictDecision:
    resolution: ConflictResolution
    winning_value: str
    losing_value: str | None
    reason: str


def resolve_conflict(
    existing_value: str,
    new_value: str,
    existing_confidence: float,
    new_confidence: float | None = None,
) -> ConflictDecision:
    """Auto-resolve by confidence (and recency — newer = higher implicit confidence)."""
    new_conf = new_confidence if new_confidence is not None else INITIAL_CONFIDENCE
    if new_conf > existing_confidence:
        return ConflictDecision(
            resolution=ConflictResolution.NEW_WINS,
            winning_value=new_value,
            losing_value=existing_value,
            reason=f"new_confidence ({new_conf:.2f}) > existing ({existing_confidence:.2f})",
        )
    if new_conf < existing_confidence:
        return ConflictDecision(
            resolution=ConflictResolution.EXISTING_WINS,
            winning_value=existing_value,
            losing_value=new_value,
            reason=f"existing_confidence ({existing_confidence:.2f}) > new ({new_conf:.2f})",
        )
    return ConflictDecision(
        resolution=ConflictResolution.NEW_WINS,
        winning_value=new_value,
        losing_value=existing_value,
        reason="equal_confidence_recency_bias",
    )
