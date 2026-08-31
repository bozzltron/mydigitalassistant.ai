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
    think: bool = False  # escalate to thinking mode (Phase 6 plan §6.2)


# Relevance threshold below which a frame is considered "not relevant"
_MIN_CITATION_RELEVANCE = 0.3

# Explicit user requests for reasoning effort (Phase 6 plan §6.2)
_EXPLICIT_THINK_MARKERS = (
    "step by step",
    "step-by-step",
    "think carefully",
    "think hard",
    "think it through",
    "think this through",
    "reason through",
    "work through",
    "take your time",
)

# Structural sequencing markers for multi-step query detection
_SEQUENCE_MARKERS = ("and then", "after that", "afterwards", "before that",
                     "first,", "first of all", "next,", "finally,")
_CLAUSE_CONJUNCTIONS = (" and then ", " then ", " after ", " while ", " because ")

_NON_INFO_PATTERNS = [
    r"^(hi|hello|hey|howdy|hiya|greetings|good morning|good afternoon|good evening)[!.?]*$",
    r"^(thanks|thank you|thx|ty)[!.?]*$",
    r"^(okay|ok|yes|yeah|yep|sure|please|yes please)[!.?]*$",
    r"^(no|nope|nah)[!.?]*$",
    r"^(bye|goodbye|see you|later|ttyl)[!.?]*$",
    r"^(wow|oh|uh-huh|hm|mhm|aha)[!.?]*$",
    r"^(nice|cool|awesome|great|good|perfect)[!.?]*$",
    r"^(?:what )?do you think[?!.]*$",
    r"^tell me a (joke|story|fact)[!.?]*$",
    r"^how are you[?!.]*$",
    r"^how('s| is) it going[?!.]*$",
    r"^what'?s up[?!.]*$",
    r"^(?:i )?(?:just|sorry|apologies?)",
    # Creative/writing follow-ups after research - don't need web search
    r"^let'?s (write|draft|compose|summarize) (the|a|my) (bio|summary|email|post|blog|article|story|poem|script|report)[!.?]*$",
    r"^(write|draft|compose|summarize) (the|a|my) (bio|summary|email|post|blog|article|story|poem|script|report)[!.?]*$",
    r"^can you (write|draft|compose|summarize) (the|a|my) (bio|summary|email|post|blog|article|story|poem|script|report)[!.?]*$",
    r"^based on (that|the research|what we found),? (write|draft|compose) (the|a|my) (bio|summary|email|post|blog|article|story|poem|script|report)[!.?]*$",
]


def _is_non_info_seeking(query: str) -> bool:
    """Return True if query is unlikely to need web search.

    Uses structural heuristics (length, question words) to avoid
    hardcoding specific vocabulary.
    """
    import re
    q = query.strip()
    words = q.split()
    word_count = len(words)
    q_lower = q.lower()

    if word_count <= 1:
        return True

    if word_count <= 3 and not _has_question_word(q_lower):
        return True

    if word_count <= 5 and not _has_info_seeking_indicator(q_lower):
        return True

    for pattern in _NON_INFO_PATTERNS:
        if re.match(pattern, q, re.IGNORECASE):
            return True

    return False


_QUESTION_WORDS = {"what", "which", "who", "whom", "whose", "where", "when", "why", "how"}
_INFO_SEEKING_PREFIXES = {"tell me", "explain", "what is", "what are", "how do", "how does",
                          "how did", "why do", "why does", "why did", "can you", "could you",
                          "would you", "is there", "are there", "show me", "find me",
                          "search for", "look up", "give me", "i want", "i need",
                          "i'm looking", "looking for", "find out", "learn about"}


def _has_question_word(text: str) -> bool:
    words = set(text.replace("?", " ").replace("!", " ").split())
    return bool(words & _QUESTION_WORDS)


def _has_info_seeking_indicator(text: str) -> bool:
    for prefix in _INFO_SEEKING_PREFIXES:
        if text.startswith(prefix) or f" {prefix}" in text:
            return True
    return False
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


def _has_explicit_think_intent(query: str) -> bool:
    """User explicitly asked for careful reasoning (§6.2 escalation trigger)."""
    q = query.lower()
    return any(marker in q for marker in _EXPLICIT_THINK_MARKERS)


def _is_multi_step(query: str) -> bool:
    """Structural detection of multi-step queries: sequencing markers or
    multiple clause-joining conjunctions in a longer query."""
    q = query.lower()
    if any(marker in q for marker in _SEQUENCE_MARKERS):
        return True
    conjunction_hits = sum(1 for c in _CLAUSE_CONJUNCTIONS if f" {c.strip()} " in f" {q} ")
    return conjunction_hits >= 2 and len(q.split()) > 12


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
    escalate = _has_explicit_think_intent(query) or (
        sufficiency in (MemorySufficiency.PARTIAL, MemorySufficiency.NONE)
        and _is_multi_step(query)
    )

    if task_type == "correction":
        return Plan(
            action=Action.CORRECT,
            sufficiency=sufficiency,
            cited_frame_ids=cited_ids,
            # Ambiguous correction validation: memory partially corroborates
            # AND partially conflicts — let thinking mode weigh it (§6.2).
            think=escalate or sufficiency == MemorySufficiency.PARTIAL,
        )

    if task_type == "search":
        return Plan(
            action=Action.SEARCH,
            sufficiency=sufficiency,
            cited_frame_ids=cited_ids,
            search_needed=True,
            think=escalate,
        )

    if task_type == "introspective":
        return Plan(
            action=Action.INTROSPECT,
            sufficiency=sufficiency,
            cited_frame_ids=cited_ids,
            introspect=True,
            think=escalate,
        )

    if sufficiency == MemorySufficiency.NONE:
        if _is_non_info_seeking(query):
            return Plan(
                action=Action.ANSWER,
                sufficiency=MemorySufficiency.NONE,
                think=_has_explicit_think_intent(query),
            )
        return Plan(
            action=Action.SEARCH,
            sufficiency=MemorySufficiency.NONE,
            search_needed=True,
            think=escalate,
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
            think=escalate,
        )

    return Plan(
        action=Action.ANSWER,
        sufficiency=MemorySufficiency.HIGH,
        cited_frame_ids=cited_ids,
        think=escalate,
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
        # Special case: if identity_name frame is in retrieved frames, use it
        # This is already boosted to relevance=1.0 by the retriever
        lines.append(
            "IMPORTANT: If the retrieved memory includes a frame named 'identity_name' "
            "with a 'full_name' slot, your name is that value. State it clearly."
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
