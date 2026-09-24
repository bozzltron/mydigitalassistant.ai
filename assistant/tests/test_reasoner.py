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

    def test_introspect_identity_name_includes_instruction(self):
        """INTROSPECT plan must instruct LLM to state identity from identity_name frame."""
        identity_frame = Frame(id=1, name="identity_name", type="entity", confidence=0.5)
        identity_slot = Slot(id=1, frame_id=1, key="full_name", value="Elysia", confidence=0.5)
        from assistant.backend.memory.retrieval import RetrievedFrame

        rf = RetrievedFrame(
            frame=identity_frame,
            slots=[identity_slot],
            associations=[],
            relevance=1.0,
            source="identity_boost",
        )
        memory = MemoryContext(
            query="what is your name?",
            retrieved_frames=[rf],
            recent_episodes=[],
            formatted="[test memory]",
        )
        plan = classify_intent("what is your name?", "introspective", memory)
        assert plan.action == Action.INTROSPECT
        text = format_plan_for_prompt(plan)
        assert "identity_name" in text
        assert "full_name" in text


class TestThinkEscalation:
    """Phase 6 §6.2: thinking-mode escalation policy."""

    def test_default_no_escalation(self):
        memory = make_memory([(make_frame(1, confidence=0.9), [], 0.8)])
        plan = classify_intent("what strings does my guitar have?", "functional", memory)
        assert plan.think is False

    def test_explicit_think_intent_escalates(self):
        for marker in ("step by step", "think carefully", "reason through this"):
            plan = classify_intent(
                f"Explain {marker} how our budget works", "functional", make_memory([])
            )
            assert plan.think is True, marker

    def test_partial_memory_plus_multi_step_escalates(self):
        memory = make_memory([(make_frame(1, confidence=0.4), [], 0.5)])
        plan = classify_intent(
            "First check my guitar strings then compare after that with bob's kayak gear",
            "functional",
            memory,
        )
        assert plan.think is True

    def test_none_memory_plus_multi_step_escalates(self):
        plan = classify_intent(
            "and then work out the schedule, because timing matters for everything else here",
            "functional",
            make_memory([]),
        )
        assert plan.think is True

    def test_high_sufficiency_multi_step_does_not_escalate(self):
        memory = make_memory([(make_frame(1, confidence=0.9), [], 0.8)])
        plan = classify_intent(
            "compare my guitar and then my amp and then my pedals for the gig",
            "functional",
            memory,
        )
        assert plan.think is False

    def test_single_step_partial_does_not_escalate(self):
        memory = make_memory([(make_frame(1, confidence=0.4), [], 0.5)])
        plan = classify_intent("what year is my guitar?", "functional", memory)
        assert plan.think is False

    def test_correction_with_partial_memory_escalates(self):
        memory = make_memory([(make_frame(1, confidence=0.4), [], 0.5)])
        plan = classify_intent("actually my guitar has 7 strings", "correction", memory)
        assert plan.think is True

    def test_search_task_can_escalate(self):
        plan = classify_intent(
            "search for a step by step guide to restring a guitar",
            "search",
            make_memory([]),
        )
        # Explicit intent marker inside a search task
        assert plan.think is True


class TestMaxIntelligenceEscalation:
    """Phase 6 M6: max-intelligence tier auto-escalation by the reasoner."""

    def test_none_memory_multi_step_search_max_escalates(self):
        plan = classify_intent(
            "and then work out the schedule, because timing matters for everything else here",
            "search",
            make_memory([]),
        )
        assert plan.think is True
        assert plan.max_intelligence is True

    def test_none_memory_multi_step_introspective_max_escalates(self):
        plan = classify_intent(
            "first, summarize what you learned about my guitar setup, "
            "and then compare after that with what you know about my barge",
            "introspective",
            make_memory([]),
        )
        assert plan.max_intelligence is True

    def test_partial_memory_multi_step_uses_thinking_only(self):
        memory = make_memory([(make_frame(1, confidence=0.4), [], 0.5)])
        plan = classify_intent(
            "First check my guitar strings then compare after that with bob's kayak gear",
            "functional",
            memory,
        )
        assert plan.think is True
        assert plan.max_intelligence is False

    def test_high_sufficiency_multi_step_no_escalation(self):
        memory = make_memory([(make_frame(1, confidence=0.9), [], 0.8)])
        plan = classify_intent(
            "compare my guitar and then my amp and then my pedals for the gig",
            "functional",
            memory,
        )
        assert plan.think is False
        assert plan.max_intelligence is False

    def test_correction_never_max_escalates(self):
        plan = classify_intent(
            "actually my guitar has 7 strings and then my amp changed too, "
            "because I swapped everything out last week",
            "correction",
            make_memory([]),
        )
        assert plan.max_intelligence is False
