"""Tests for the reasoner: assess_memory_sufficiency and classify_intent."""

from assistant.backend.memory.models import Frame, Slot
from assistant.backend.memory.retrieval import MemoryContext, RetrievedFrame
from assistant.backend.pipeline.reasoner import (
    Action,
    MemorySufficiency,
    assess_memory_sufficiency,
    classify_intent,
    format_plan_for_prompt,
)


def make_frame(
    frame_id: int,
    confidence: float = 0.5,
    priority: float = 0.5,
) -> Frame:
    return Frame(
        id=frame_id,
        name=f"frame_{frame_id}",
        type="entity",
        confidence=confidence,
        priority=priority,
    )


def make_memory(
    frames: list[tuple[Frame, list[Slot], float]],
) -> MemoryContext:
    retrieved_frames = [
        RetrievedFrame(
            frame=frame,
            slots=slots,
            associations=[],
            relevance=relevance,
            source="direct_match",
        )
        for frame, slots, relevance in frames
    ]
    return MemoryContext(
        query="test query",
        retrieved_frames=retrieved_frames,
        recent_episodes=[],
        formatted="[test memory]",
    )


class TestAssessMemorySufficiency:
    def test_no_frames_returns_none(self):
        memory = make_memory([])
        sufficiency, cited = assess_memory_sufficiency("test", memory)
        assert sufficiency == MemorySufficiency.NONE
        assert cited == []

    def test_low_relevance_frame_returns_none(self):
        frame = make_frame(1, confidence=0.8)
        memory = make_memory([(frame, [], 0.1)])  # below _MIN_CITATION_RELEVANCE
        sufficiency, cited = assess_memory_sufficiency("test", memory)
        assert sufficiency == MemorySufficiency.NONE

    def test_partial_sufficiency(self):
        frame = make_frame(1, confidence=0.4)  # below _HIGH_CONFIDENCE_THRESHOLD
        memory = make_memory([(frame, [], 0.5)])
        sufficiency, cited = assess_memory_sufficiency("test", memory)
        assert sufficiency == MemorySufficiency.PARTIAL
        assert 1 in cited

    def test_high_sufficiency(self):
        frame = make_frame(1, confidence=0.8)  # >= _HIGH_CONFIDENCE_THRESHOLD
        memory = make_memory([(frame, [], 0.8)])
        sufficiency, cited = assess_memory_sufficiency("test", memory)
        assert sufficiency == MemorySufficiency.HIGH
        assert 1 in cited

    def test_multiple_frames_average_confidence(self):
        f1 = make_frame(1, confidence=0.9)
        f2 = make_frame(2, confidence=0.3)  # average = 0.6 -> HIGH
        memory = make_memory([(f1, [], 0.8), (f2, [], 0.8)])
        sufficiency, cited = assess_memory_sufficiency("test", memory)
        assert sufficiency == MemorySufficiency.HIGH
        assert 1 in cited and 2 in cited


class TestClassifyIntent:
    def test_introspective_returns_introspect(self):
        frame = make_frame(1, confidence=0.8)
        memory = make_memory([(frame, [], 0.8)])
        plan = classify_intent("what do you know about guitars?", "introspective", memory)
        assert plan.action == Action.INTROSPECT
        assert plan.introspect is True
        assert plan.sufficiency == MemorySufficiency.HIGH

    def test_functional_no_memory_search_needed(self):
        memory = make_memory([])
        plan = classify_intent("what is the weather today?", "functional", memory)
        assert plan.action == Action.SEARCH
        assert plan.search_needed is True

    def test_greeting_no_memory_no_search(self):
        memory = make_memory([])
        plan = classify_intent("Hello", "functional", memory)
        assert plan.action == Action.ANSWER
        assert plan.search_needed is False

    def test_thanks_no_memory_no_search(self):
        memory = make_memory([])
        plan = classify_intent("Thanks!", "functional", memory)
        assert plan.action == Action.ANSWER
        assert plan.search_needed is False

    def test_ok_no_memory_no_search(self):
        memory = make_memory([])
        plan = classify_intent("Okay", "functional", memory)
        assert plan.action == Action.ANSWER
        assert plan.search_needed is False

    def test_search_task_type_triggers_search(self):
        memory = make_memory([])
        plan = classify_intent("search for capybaras", "search", memory)
        assert plan.action == Action.SEARCH
        assert plan.search_needed is True

    def test_functional_high_memory_no_search_keywords(self):
        frame = make_frame(1, confidence=0.8)
        memory = make_memory([(frame, [], 0.8)])
        plan = classify_intent("tell me about my guitar", "functional", memory)
        assert plan.action == Action.ANSWER
        assert plan.search_needed is False
        assert plan.sufficiency == MemorySufficiency.HIGH

    def test_functional_partial_memory_returns_answer_with_gaps(self):
        frame = make_frame(1, confidence=0.4)
        memory = make_memory([(frame, [], 0.5)])
        plan = classify_intent("what can you tell me about that?", "functional", memory)
        assert plan.action == Action.ANSWER
        assert plan.search_needed is False
        assert plan.sufficiency == MemorySufficiency.PARTIAL
        assert len(plan.knowledge_gaps) > 0

    def test_correction_task_type_triggers_correct(self):
        memory = make_memory([])
        plan = classify_intent("actually, the guitar has 12 strings", "correction", memory)
        assert plan.action == Action.CORRECT
        assert plan.search_needed is False


class TestFormatPlanForPrompt:
    def test_answer_high_sufficiency(self):
        from assistant.backend.pipeline.reasoner import Plan

        plan = Plan(
            action=Action.ANSWER,
            sufficiency=MemorySufficiency.HIGH,
            cited_frame_ids=[1, 2],
        )
        text = format_plan_for_prompt(plan)
        assert "[MEMORY] Answer from the retrieved memory" in text
        assert "ids [1, 2]" in text

    def test_answer_partial_includes_note(self):
        from assistant.backend.pipeline.reasoner import Plan

        plan = Plan(
            action=Action.ANSWER,
            sufficiency=MemorySufficiency.PARTIAL,
            knowledge_gaps=["partial info"],
        )
        text = format_plan_for_prompt(plan)
        assert "[MEMORY]" in text
        assert "partial" in text.lower() or "gaps" in text.lower()

    def test_search_action(self):
        from assistant.backend.pipeline.reasoner import Plan

        plan = Plan(
            action=Action.SEARCH,
            sufficiency=MemorySufficiency.NONE,
            search_needed=True,
        )
        text = format_plan_for_prompt(plan)
        assert "[SEARCH]" in text

    def test_introspect_action(self):
        from assistant.backend.pipeline.reasoner import Plan

        plan = Plan(
            action=Action.INTROSPECT,
            sufficiency=MemorySufficiency.HIGH,
            cited_frame_ids=[1],
        )
        text = format_plan_for_prompt(plan)
        assert "[INTROSPECT]" in text

    def test_correct_action(self):
        from assistant.backend.pipeline.reasoner import Plan

        plan = Plan(
            action=Action.CORRECT,
            sufficiency=MemorySufficiency.HIGH,
        )
        text = format_plan_for_prompt(plan)
        assert "[CORRECTION]" in text
