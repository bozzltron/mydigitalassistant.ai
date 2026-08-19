from dataclasses import dataclass
from enum import Enum

INITIAL_CONFIDENCE = 0.5
REPEAT_DISCOUNT = 0.7  # new_conf = 1 - (1 - old_conf) * 0.7
MAX_CONFIDENCE = 0.99

INITIAL_PRIORITY = 0.5
PRIORITY_BUMP = 0.2  # "remember this" bumps priority by this amount
MAX_PRIORITY = 1.0
FORGOTTEN_PRIORITY = 0.0

# Default source reliability by source_type
DEFAULT_RELIABILITY = {
    "user": 0.95,
    "user_correction": 0.95,
    "manual_override": 1.0,
    "search": 0.5,
    "inference": 0.6,
    "imported": 0.7,
}


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


def bump_priority(current: float) -> float:
    """'Remember this' — bump priority, capped at MAX_PRIORITY."""
    return min(current + PRIORITY_BUMP, MAX_PRIORITY)


def max_priority() -> float:
    """'Remember this forever' — set priority to maximum, caller also sets essential=1."""
    return MAX_PRIORITY


def forget_priority() -> float:
    """'Forget this' — soft-delete by setting priority to 0."""
    return FORGOTTEN_PRIORITY


def default_source_reliability(source_type: str | None) -> float:
    """Return the default reliability for a source type.

    User-provided facts are most reliable; search results are least reliable.
    Returns 0.5 (unknown reliability) for unrecognized or None source types.
    """
    if source_type is None:
        return 0.5
    return DEFAULT_RELIABILITY.get(source_type, 0.5)


def initial_priority() -> float:
    return INITIAL_PRIORITY


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
    existing_source_reliability: float | None = None,
    new_source_reliability: float | None = None,
    existing_priority: float | None = None,
    new_priority: float | None = None,
) -> ConflictDecision:
    """Auto-resolve by source_reliability > confidence > priority > recency.

    Resolution order:
    1. Higher source_reliability wins (sources like 'user_correction' are more trusted).
    2. If equal reliability, higher confidence wins.
    3. If equal confidence, higher priority wins.
    4. If all equal, recency bias: new value wins.
    """
    new_conf = new_confidence if new_confidence is not None else INITIAL_CONFIDENCE
    existing_rel = (
        existing_source_reliability
        if existing_source_reliability is not None
        else INITIAL_CONFIDENCE
    )
    new_rel = (
        new_source_reliability
        if new_source_reliability is not None
        else INITIAL_CONFIDENCE
    )
    existing_pri = (
        existing_priority if existing_priority is not None else INITIAL_PRIORITY
    )
    new_pri = new_priority if new_priority is not None else INITIAL_PRIORITY

    if new_rel != existing_rel:
        if new_rel > existing_rel:
            return ConflictDecision(
                resolution=ConflictResolution.NEW_WINS,
                winning_value=new_value,
                losing_value=existing_value,
                reason=f"new_source_reliability ({new_rel:.2f}) > existing ({existing_rel:.2f})",
            )
        return ConflictDecision(
            resolution=ConflictResolution.EXISTING_WINS,
            winning_value=existing_value,
            losing_value=new_value,
            reason=f"existing_source_reliability ({existing_rel:.2f}) > new ({new_rel:.2f})",
        )

    if new_conf != existing_confidence:
        if new_conf > existing_confidence:
            return ConflictDecision(
                resolution=ConflictResolution.NEW_WINS,
                winning_value=new_value,
                losing_value=existing_value,
                reason=f"new_confidence ({new_conf:.2f}) > existing ({existing_confidence:.2f})",
            )
        return ConflictDecision(
            resolution=ConflictResolution.EXISTING_WINS,
            winning_value=existing_value,
            losing_value=new_value,
            reason=f"existing_confidence ({existing_confidence:.2f}) > new ({new_conf:.2f})",
        )

    if new_pri != existing_pri:
        if new_pri > existing_pri:
            return ConflictDecision(
                resolution=ConflictResolution.NEW_WINS,
                winning_value=new_value,
                losing_value=existing_value,
                reason=f"new_priority ({new_pri:.2f}) > existing ({existing_pri:.2f})",
            )
        return ConflictDecision(
            resolution=ConflictResolution.EXISTING_WINS,
            winning_value=existing_value,
            losing_value=new_value,
            reason=f"existing_priority ({existing_pri:.2f}) > new ({new_pri:.2f})",
        )

    return ConflictDecision(
        resolution=ConflictResolution.NEW_WINS,
        winning_value=new_value,
        losing_value=existing_value,
        reason="equal_all_recency_bias",
    )
