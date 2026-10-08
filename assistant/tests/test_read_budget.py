"""The read budget and paging.

A tool result must not be able to fill the model's context window on its own, and
a capped read must tell the model the range and how to read on.

Regression: a 43k-char CSV made the tool loop's prompt ~16.3k tokens against a
16,384 window; Ollama truncated it below us and the model answered from ~150 of
626 rows with no signal that it had. The old cap (60,000 chars ~= 15k tokens)
allowed one read to nearly fill the window by itself.
"""

from __future__ import annotations

import pytest

from assistant.backend.config import settings
from assistant.backend.pipeline.context_budget import (
    CHARS_PER_TOKEN,
    FALLBACK_WINDOW_FRACTION,
)
from assistant.backend.pipeline.tool_executor import (
    _bounded_for_model,
    _page_and_bound,
    _page_text,
    _read_char_limit,
)


def test_read_limit_falls_back_to_a_fraction_without_a_turn_budget():
    """A direct tool call (no turn budget) still cannot fill the window.

    Inside a turn the limit is derived from the measured fixed cost
    (context_budget.py); outside one -- the CLI, a direct call -- it falls back to
    a fraction of the configured chat window, the pre-T2 behaviour.
    """
    expected = int(settings.chat_num_ctx * FALLBACK_WINDOW_FRACTION) * CHARS_PER_TOKEN
    assert _read_char_limit() == max(2_000, expected)
    # A single read can no longer be the whole window.
    assert _read_char_limit() < settings.chat_num_ctx * CHARS_PER_TOKEN


def test_a_short_read_is_untouched():
    assert _bounded_for_model("hello") == "hello"


def test_a_capped_read_names_the_range_and_the_next_page():
    text = "\n".join(f"line {i}" for i in range(20_000))
    out = _bounded_for_model(text, handle="data.csv")
    assert "truncated" in out
    assert "The rest was NOT read" in out
    assert "of 20000" in out  # names the total line count
    assert "read_file(path='data.csv', offset=" in out  # the exact next call


def test_paging_slices_lines():
    text = "\n".join(f"line {i}" for i in range(100))
    page, start, total = _page_text(text, offset=10, limit=5)
    assert page.splitlines() == [f"line {i}" for i in range(10, 15)]
    assert start == 10
    assert total == 100


def test_paging_default_reads_everything():
    page, start, total = _page_text("a\nb\nc", offset=0, limit=None)
    assert page == "a\nb\nc"
    assert start == 0
    assert total == 3


def test_a_requested_page_names_its_range():
    """A page that fits still says where it is, so the model is not lost."""
    text = "\n".join(f"line {i}" for i in range(100))
    out, total = _page_and_bound(text, offset=10, limit=5, handle="data.csv")
    assert out.startswith("[lines 11-15 of 100]\n")
    assert "line 10" in out and "line 14" in out
    assert "line 15" not in out
    assert total == 100


@pytest.mark.asyncio
async def test_a_big_csv_read_is_capped_and_actionable(store):
    """End to end: a CSV past the budget is capped, and the marker says how to
    read the rest."""
    from assistant.backend.pipeline import filesystem
    from assistant.backend.pipeline.tool_executor import execute_read_file

    filesystem.SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    path = filesystem.SANDBOX_ROOT / "phase2_big.csv"
    path.write_text(
        "email,name\n" + "\n".join(f"a{i}@x.com,Name {i}" for i in range(6000)),
        encoding="utf-8",
    )
    try:
        result = await execute_read_file(
            {"path": "phase2_big.csv"}, user_id="1", session_id="s"
        )
        assert result.success, result.error
        content = result.data["content"]
        assert "truncated" in content
        assert "offset=" in content and "phase2_big.csv" in content
        assert result.data["total_lines"] == 6001
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_a_capped_read_result_fits_the_allowance_including_metadata():
    """The rendered tool result, not just its content, must fit the allowance.

    Regression (`context_budget_value`): the content was capped, but `str(data)` --
    content plus frame ids, sizes and totals -- exceeded the allowance by the dict
    overhead, and the truncation marker sat outside the cap too.
    """
    from assistant.backend.pipeline import filesystem
    from assistant.backend.pipeline.context_budget import (
        TurnBudget,
        content_char_limit,
        reset_turn_budget,
        set_turn_budget,
    )
    from assistant.backend.pipeline.tool_executor import execute_read_file

    filesystem.SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    path = filesystem.SANDBOX_ROOT / "render_budget.csv"
    path.write_text(
        "email,name\n" + "\n".join(f"a{i}@x.com,Name {i}" for i in range(6000)),
        encoding="utf-8",
    )
    token = set_turn_budget(
        TurnBudget(
            window_tokens=16384, fixed_cost_chars=0, reserved_output_tokens=16384 - 1000
        )
    )
    try:
        result = await execute_read_file(
            {"path": "render_budget.csv"}, user_id="1", session_id="s"
        )
        limit = content_char_limit()
    finally:
        reset_turn_budget(token)
        path.unlink(missing_ok=True)

    assert result.success, result.error
    assert "truncated" in result.data["content"]
    assert len(str(result.data)) <= limit, f"{len(str(result.data))} > {limit}"
