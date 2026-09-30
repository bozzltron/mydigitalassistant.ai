"""Memory-grounded answer confidence (0-1)."""

from types import SimpleNamespace

from assistant.backend.pipeline.orchestrator import compute_answer_confidence


def _frame(confidence: float, relevance: float):
    return SimpleNamespace(
        frame=SimpleNamespace(confidence=confidence), relevance=relevance
    )


def test_none_when_no_frames_and_no_search():
    assert compute_answer_confidence([], None) == (0.0, "none")


def test_memory_is_average_of_frames_that_clear_the_relevance_bar():
    frames = [_frame(0.8, 0.9), _frame(0.6, 0.5), _frame(0.1, 0.1)]
    assert compute_answer_confidence(frames, None) == (0.7, "memory")


def test_search_corroboration_beats_low_memory():
    info = SimpleNamespace(
        results=[
            SimpleNamespace(url="https://a.example/1"),
            SimpleNamespace(url="https://b.example/2"),
            SimpleNamespace(url="https://a.example/3"),
        ]
    )
    # Two distinct domains -> 0.5 + 0.1
    assert compute_answer_confidence([], info) == (0.6, "search")


def test_memory_wins_when_higher_than_search():
    frames = [_frame(0.9, 0.8)]
    info = SimpleNamespace(results=[SimpleNamespace(url="https://a.example/1")])
    assert compute_answer_confidence(frames, info) == (0.9, "memory")
