from unittest.mock import AsyncMock

import pytest

from assistant.backend.pipeline.task_router import (
    TaskType,
    classify,
    classify_heuristic,
    classify_with_llm,
    route,
)


@pytest.mark.parametrize(
    "query",
    [
        "What do you know about what you remember?",
        "Do you remember my dog?",
        "What have we talked about?",
        "What have you learned about guitars?",
    ],
)
def test_heuristic_introspective_patterns(query):
    assert classify_heuristic(query) == TaskType.INTROSPECTIVE


def test_heuristic_introspective_do_you_remember():
    assert classify_heuristic("Do you remember my dog?") == TaskType.INTROSPECTIVE


def test_heuristic_introspective_what_have_we_talked():
    assert classify_heuristic("What have we talked about?") == TaskType.INTROSPECTIVE


def test_heuristic_introspective_have_you_heard():
    # "Have you heard of X" is ambiguous - could be asking model's knowledge (introspective)
    # or asking it to search (functional). Heuristic returns None, LLM decides.
    assert classify_heuristic("Have you heard of quantum computing?") is None


def test_heuristic_introspective_case_insensitive():
    assert classify_heuristic("WHAT DO YOU REMEMBER?") == TaskType.INTROSPECTIVE


def test_heuristic_introspective_tell_me_about_article():
    # Ambiguous - LLM should decide. Heuristic returns None.
    assert classify_heuristic("Tell me more about the Glasgow article") is None
    assert classify_heuristic("Tell me about the story") is None
    assert classify_heuristic("Tell me about the topic") is None


def test_heuristic_introspective_pick_up():
    """Queries referencing prior context should be introspective (LLM decides)."""
    assert classify_heuristic("Pick up with that") is None
    assert classify_heuristic("Pick up on that") is None
    assert classify_heuristic("It was on apnews.com") is None
    assert classify_heuristic("That was related to climate") is None


def test_heuristic_introspective_recall_about():
    # "What do you recall" matches heuristic, "what was discussed" goes to LLM
    assert (
        classify_heuristic("What do you recall about the Glasgow article?")
        == TaskType.INTROSPECTIVE
    )
    assert classify_heuristic("What was discussed about climate change?") is None


def test_heuristic_returns_none_for_functional():
    assert classify_heuristic("How does a guitar amplifier work?") is None
    assert classify_heuristic("Search for recipes") is None
    assert classify_heuristic("Set a reminder for tomorrow") is None


async def test_llm_classify_introspective():
    mock_llm = AsyncMock()
    mock_llm.chat.return_value.content = '{"task_type": "introspective"}'
    mock_llm.utility_model = "qwen2.5:3b"

    result = await classify_with_llm("What do you think about X?", mock_llm)
    assert result.task_type == TaskType.INTROSPECTIVE
    assert result.wants_search is None  # field absent -> unknown


async def test_llm_classify_functional():
    mock_llm = AsyncMock()
    mock_llm.chat.return_value.content = '{"task_type": "functional"}'
    mock_llm.utility_model = "qwen2.5:3b"

    result = await classify_with_llm("How does X work?", mock_llm)
    assert result.task_type == TaskType.FUNCTIONAL


async def test_llm_classify_malformed_falls_back_to_functional():
    mock_llm = AsyncMock()
    mock_llm.chat.return_value.content = "not json"
    mock_llm.utility_model = "qwen2.5:3b"

    result = await classify_with_llm("blah", mock_llm)
    assert result.task_type == TaskType.FUNCTIONAL


async def test_route_parses_wants_search():
    """Storage statements carry wants_search=false in the same LLM pass."""
    mock_llm = AsyncMock()
    mock_llm.chat.return_value.content = (
        '{"task_type": "functional", "wants_search": false}'
    )
    mock_llm.utility_model = "qwen2.5:3b"

    result = await route("Remember that my guitar has 6 strings.", mock_llm)
    assert result.task_type == TaskType.FUNCTIONAL
    assert result.wants_search is False


async def test_route_wants_search_invalid_type_becomes_none():
    mock_llm = AsyncMock()
    mock_llm.chat.return_value.content = (
        '{"task_type": "functional", "wants_search": "maybe"}'
    )
    mock_llm.utility_model = "qwen2.5:3b"

    result = await route("Explain photosynthesis", mock_llm)
    assert result.wants_search is None


async def test_route_heuristic_match_has_no_search_signal():
    mock_llm = AsyncMock()
    result = await route("What do you remember about dogs?", mock_llm)
    assert result.task_type == TaskType.INTROSPECTIVE
    assert result.wants_search is None
    mock_llm.chat.assert_not_called()


async def test_classify_uses_heuristic_first():
    """If heuristic matches, LLM should NOT be called."""
    mock_llm = AsyncMock()
    result = await classify("What do you remember about dogs?", mock_llm)
    assert result == TaskType.INTROSPECTIVE
    mock_llm.chat.assert_not_called()


async def test_classify_falls_back_to_llm_when_ambiguous():
    """If heuristic doesn't match, LLM should be called."""
    mock_llm = AsyncMock()
    mock_llm.chat.return_value.content = '{"task_type": "functional"}'
    mock_llm.utility_model = "qwen2.5:3b"

    result = await classify("Explain how photosynthesis works", mock_llm)
    assert result == TaskType.FUNCTIONAL
    mock_llm.chat.assert_called_once()


async def test_classify_no_llm_fallback_defaults_to_functional():
    """When use_llm_fallback=False, ambiguous -> functional."""
    mock_llm = AsyncMock()
    result = await classify("Random ambiguous query", mock_llm, use_llm_fallback=False)
    assert result == TaskType.FUNCTIONAL
    mock_llm.chat.assert_not_called()


async def test_classify_handles_llm_exception_by_falling_back_to_functional():
    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.side_effect = RuntimeError("LLM unavailable")

    result = await classify("Explain quantum computing", mock_llm)
    assert result == TaskType.FUNCTIONAL
