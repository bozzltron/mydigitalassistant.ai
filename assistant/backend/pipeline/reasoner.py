"""Reasoner: decides what to do with retrieved memory before calling the LLM.

The reasoner is the "thinking before speaking" step:
1. Assess whether the retrieved memory is sufficient to answer the query.
2. Decide on an action: answer from memory, trigger search, or ask for clarification.
3. Produce a Plan that guides how the LLM should respond.

This replaces the ad-hoc keyword heuristic for search decisions in the orchestrator.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from assistant.backend.memory.retrieval import MemoryContext


class MemorySufficiency(StrEnum):
    HIGH = "high"  # confident, relevant frames cover the query
    PARTIAL = "partial"  # some relevant memory, but gaps or low confidence
    NONE = "none"  # no relevant memory found


class Action(StrEnum):
    ANSWER = "answer"  # answer from retrieved memory
    SEARCH = "search"  # external search needed before answering
    INTROSPECT = "introspect"  # reflect on memory (no external info needed)
    CORRECT = "correct"  # user is correcting a stored fact


@dataclass
class Plan:
    action: Action
    sufficiency: MemorySufficiency
    knowledge_gaps: list[str] = field(default_factory=list)
    cited_frame_ids: list[int] = field(default_factory=list)
    search_needed: bool = False
    introspect: bool = False
    correction_frame: str | None = None  # frame name the user is correcting
    correction_slot: str | None = None  # slot key the user is correcting
    correction_value: str | None = None  # the correct value


# Relevance threshold below which a frame is considered "not relevant"
_MIN_CITATION_RELEVANCE = 0.3
# Minimum average frame confidence for HIGH sufficiency
_HIGH_CONFIDENCE_THRESHOLD = 0.6
# Minimum number of relevant frames for PARTIAL sufficiency
_MIN_PARTIAL_FRAME_COUNT = 1


def assess_memory_sufficiency(
    query: str,
    memory: MemoryContext,
) -> tuple[MemorySufficiency, list[int]]:
    """Assess whether the retrieved memory is sufficient to answer the query.

    Returns (sufficiency, cited_frame_ids).
    cited_frame_ids are those with relevance >= _MIN_CITATION_RELEVANCE.
    """
    relevant = [
        rf for rf in memory.retrieved_frames
        if rf.relevance >= _MIN_CITATION_RELEVANCE
    ]

    if not relevant:
        return MemorySufficiency.NONE, []

    cited_ids = [rf.frame.id for rf in relevant]

    avg_confidence = sum(rf.frame.confidence for rf in relevant) / len(relevant)

    if len(relevant) >= _MIN_PARTIAL_FRAME_COUNT and avg_confidence >= _HIGH_CONFIDENCE_THRESHOLD:
        return MemorySufficiency.HIGH, cited_ids

    return MemorySufficiency.PARTIAL, cited_ids


def classify_intent(
    query: str,
    task_type: str,
    memory: MemoryContext,
) -> Plan:
    """Decide the action and memory sufficiency for a query.

    Classification (task_type) is done once by the task router.
    Here we map task_type to an Action and assess memory sufficiency.
    """
    sufficiency, cited_ids = assess_memory_sufficiency(query, memory)

    if task_type == "correction":
        return Plan(
            action=Action.CORRECT,
            sufficiency=sufficiency,
            cited_frame_ids=cited_ids,
        )

    if task_type == "search":
        return Plan(
            action=Action.SEARCH,
            sufficiency=sufficiency,
            cited_frame_ids=cited_ids,
            search_needed=True,
        )

    if task_type == "introspective":
        return Plan(
            action=Action.INTROSPECT,
            sufficiency=sufficiency,
            cited_frame_ids=cited_ids,
            introspect=True,
        )

    if sufficiency == MemorySufficiency.NONE:
        return Plan(
            action=Action.SEARCH,
            sufficiency=MemorySufficiency.NONE,
            search_needed=True,
        )

    if sufficiency == MemorySufficiency.PARTIAL:
        gaps = [
            f"Memory has partial info for: {query[:50]}. "
            "Answer from memory and note gaps."
        ]
        return Plan(
            action=Action.ANSWER,
            sufficiency=MemorySufficiency.PARTIAL,
            cited_frame_ids=cited_ids,
            knowledge_gaps=gaps,
        )

    return Plan(
        action=Action.ANSWER,
        sufficiency=MemorySufficiency.HIGH,
        cited_frame_ids=cited_ids,
    )


def format_plan_for_prompt(plan: Plan) -> str:
    """Format a Plan as instructions to inject into the system prompt."""
    lines = []
    if plan.action == Action.ANSWER:
        lines.append("[MEMORY] Answer from the retrieved memory below.")
        if plan.sufficiency == MemorySufficiency.PARTIAL:
            lines.append(
                "[NOTE] The memory is partial — acknowledge gaps and avoid over-stating confidence."
            )
    elif plan.action == Action.SEARCH:
        lines.append(
            "[SEARCH] External information may be needed. "
            "If search results are provided, answer from both memory and search. "
            "If search fails, say so honestly."
        )
    elif plan.action == Action.INTROSPECT:
        lines.append(
            "[INTROSPECT] The user is asking about YOUR memory. "
            "Ground your answer ONLY in the retrieved memory. "
            "Cite specific frames and episodes."
        )
    elif plan.action == Action.CORRECT:
        lines.append(
            "[CORRECTION] The user is correcting a stored fact. "
            "Acknowledge the correction gracefully and confirm the updated information."
        )

    if plan.cited_frame_ids:
        lines.append(
            f"[CITATION] Relevant frames: ids {plan.cited_frame_ids}. "
            "Reference them explicitly in your answer."
        )

    return " ".join(lines)
