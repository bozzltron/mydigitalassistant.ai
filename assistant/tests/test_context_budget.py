"""The per-turn context budget: content allowance derived from the fixed cost.

T2 of the context-throughput work. A tool result must not fill the window, and
the allowance for one must account for what the window already holds (system
prompt, tool schemas, history, the user turn) and for the answer -- rather than a
flat fraction of the window that cannot see any of it. See
docs/CONTEXT_THROUGHPUT.md.
"""

from __future__ import annotations

import pytest

from assistant.backend.pipeline.context_budget import (
    CHARS_PER_TOKEN,
    MIN_CONTENT_CHARS,
    RESERVED_OUTPUT_TOKENS,
    TurnBudget,
    content_char_limit,
    measure_budget,
    reset_turn_budget,
    set_turn_budget,
)


def test_allowance_subtracts_the_measured_fixed_cost_and_the_answer():
    budget = measure_budget(
        16384,
        system_prompt_chars=12000,
        tool_schema_chars=17778,
        history_chars=2000,
        user_message_chars=200,
    )
    fixed_chars = 12000 + 17778 + 2000 + 200
    assert budget.fixed_cost_chars == fixed_chars
    # Round the fixed cost up: under-counting it over-states the allowance.
    assert budget.fixed_cost_tokens == -(-fixed_chars // CHARS_PER_TOKEN)
    expected_tokens = 16384 - budget.fixed_cost_tokens - RESERVED_OUTPUT_TOKENS
    assert budget.content_tokens == expected_tokens
    assert budget.content_chars == expected_tokens * CHARS_PER_TOKEN


def test_a_longer_history_shrinks_the_allowance():
    short = measure_budget(16384, tool_schema_chars=17778)
    long = measure_budget(16384, tool_schema_chars=17778, history_chars=40000)
    assert long.content_chars < short.content_chars


def test_the_allowance_has_a_floor():
    """A window smaller than the fixed cost still yields a usable read."""
    budget = TurnBudget(window_tokens=100, fixed_cost_chars=100_000)
    assert budget.content_tokens == 0
    assert budget.content_chars == MIN_CONTENT_CHARS


def test_content_char_limit_uses_the_turn_budget_when_set():
    fallback = content_char_limit()
    token = set_turn_budget(TurnBudget(window_tokens=16384, fixed_cost_chars=0))
    try:
        assert content_char_limit() == (16384 - RESERVED_OUTPUT_TOKENS) * CHARS_PER_TOKEN
    finally:
        reset_turn_budget(token)
    # Reset restores the fallback, so a stale budget cannot leak to the next turn.
    assert content_char_limit() == fallback


def test_cap_entries_to_budget_trims_and_reports_the_total():
    """A listing is trimmed to the allowance, but `count` stays the true total."""
    from assistant.backend.pipeline.tool_executor import _cap_entries_to_budget

    token = set_turn_budget(
        TurnBudget(window_tokens=16384, fixed_cost_chars=0, reserved_output_tokens=16384 - 250)
    )
    try:
        entries = [{"name": f"file_{i}.txt", "path": f"dir/file_{i}.txt"} for i in range(200)]
        kept, total = _cap_entries_to_budget(entries)
    finally:
        reset_turn_budget(token)
    assert total == 200
    assert 0 < len(kept) < 200


def test_orchestrator_installs_and_resets_the_budget():
    """The budget is set around the tool loop and reset after it."""
    import inspect

    from assistant.backend.pipeline import orchestrator as orch_module

    source = inspect.getsource(orch_module.Orchestrator._run_turn)
    assert "measure_budget(" in source
    assert "set_turn_budget(" in source
    assert "reset_turn_budget(" in source


@pytest.mark.asyncio
async def test_search_file_respects_the_turn_allowance():
    """A tight allowance stops search_file collecting long matches.

    The first match is always kept; past the allowance the result is marked
    truncated and still reports how many there were.
    """
    from assistant.backend.pipeline import filesystem
    from assistant.backend.pipeline.tool_executor import execute_search_file

    filesystem.SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    path = filesystem.SANDBOX_ROOT / "budget_search.txt"
    path.write_text(
        "\n".join(f"match {'x' * 250} {i}" for i in range(500)), encoding="utf-8"
    )
    token = set_turn_budget(
        TurnBudget(window_tokens=16384, fixed_cost_chars=0, reserved_output_tokens=16384 - 1000)
    )
    try:
        result = await execute_search_file(
            {"path": "budget_search.txt", "query": "match"}, "1", "s"
        )
    finally:
        reset_turn_budget(token)
        path.unlink(missing_ok=True)

    assert result.success, result.error
    assert result.data["truncated"] is True
    assert result.data["match_count"] < 80  # under the per-count cap: char-capped
    assert result.data["total_matches"] == 500
