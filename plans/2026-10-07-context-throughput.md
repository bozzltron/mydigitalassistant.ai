---
date: 2026-10-07
status: active
estimated_hours: 8
---

# Context throughput: is it optimized for the goals?

## Objective

Make the turn's context throughput **explicit, measured, and budgeted** so it
scales with every call a single response flow can make — not sized by per-item
guesses over a shared window.

The architecture map (windows, caps, composition, arithmetic, bottlenecks) lives
in **`docs/CONTEXT_THROUGHPUT.md`**. This plan is the *work*: what to change, in
what order, and how we will know it worked.

Goals it is measured against: privacy-first local inference; household scale; a
fast first token; and the rule that the agent never silently answers from a
fragment.

## Progress

- **T1 — Instrument the composition. SHIPPED (2026-10-07).**
  - `stream_tool_loop` counts the total characters of tool results appended and
    carries it on `FinalizeEvent.tool_result_chars` (serialized on the wire).
  - `_run_turn` logs `context_fixed:` — `system_prompt_chars`,
    `tool_schema_chars`, `history_chars`, `tool_result_chars`, with the peak
    prompt tokens and window — next to `context_usage:`.
  - No behavior change; the budget work is now driven by where the chars go.
  - Tests: `test_streaming_events.py` (the loop reports the total; the wire
    format carries it) and `test_turn_timings.py` (the line is logged at INFO).

## Fact-check of the map

Corrected while writing it down, so the map is not a guess:

- `glob` **is** capped — at `filesystem.MAX_GLOB_RESULTS` (1,000). The earlier
  draft implied it was uncapped. `list_files`' frame enrichment is **not** capped.
- The first `tool_result_chars` implementation read back `0` on every turn
  because `serialize_event` did not include the field — the log would have been
  a silent lie. Fixed and pinned by a test.
- The measured tool-schema size is **17,778 chars** (19 tools), not the
  `assistant/AGENTS.md` figure of ~16.6k. Corrected in AGENTS.md.

## Is it optimized for the goals?

**Safe, not yet optimized.** The large-file work made the window visible, marked,
and pageable — nothing drops silently. But throughput is still per-item fractions
over a shared window with a large, partly-duplicated fixed cost. The cheapest wins
are not a bigger model:

- **T2 — one per-turn budget.** Content allowance = `num_ctx − measured fixed
  cost − reserved answer`, split across read/search/list/fetch. This is what makes
  it scale; it is also what the large-file plan *specified* but did not ship
  (`_read_char_limit` uses a flat `× 0.35`).
- **T3 — an aggregate tool-result cap** across the loop's rounds, not per result.
- **T4 — lower the fixed cost:** de-duplicate the prose tool list; bound history
  by tokens, not turns.
- **T5 — re-measure.**

A larger-context model (`assistant/experiments/model_swap_qwen35_claude/`, 262k)
changes the arithmetic but not the design. **Opus evaluation comes after T2–T5**,
so it is measured against a budgeted pipeline rather than hiding the problem.

## Phases

| # | Work | Status |
|---|---|---|
| T1 | Instrument the composition (`context_fixed`) | **shipped** |
| T2 | One per-turn budget object; wire read/search/list/fetch to it | next |
| T3 | Aggregate tool-result cap across rounds | |
| T4 | De-duplicate tool prose; token-bound history | |
| T5 | Re-measure on a fixed turn set | |
| O1 | Run the opus model-swap experiment against the improved pipeline | after T5 |

## Test strategy

- **T1 (shipped):** the loop reports `tool_result_chars`; the wire format carries
  it; the line is logged at INFO.
- **T2/T3 (unit):** the budget split (fixed cost → content allowance); the
  aggregate cap shrinks later results.
- **Regression (the point):** the 43k-char CSV turn never reaches the window and
  always carries a marker naming the next page.
- **Measurement:** `context_usage` peak and `pct` before/after on a fixed turn
  set, recorded here.

## How we will know it worked

- The tool loop's peak prompt stays below the window with headroom on the fixed
  turn set, and `context_usage` `truncated` is never `True`.
- A `list_files` on a full sandbox cannot approach the window by itself.
- The 43k-char CSV turn pages to what it needs, and its peak prompt is lower than
  today's.

## Threats and limits

- **We do not tokenize locally.** Every chars→tokens figure is a ÷4 estimate; the
  true count is `prompt_eval_count`.
- **A bigger model hides, not fixes, composability.**
- **Trimming the prose tool list risks the model not knowing a tool exists** —
  measure, do not assume.
- **This is a map, not a mandate.** T2–T4 are candidates; the measurement decides.

## Rollback

T1 changes no behavior. T2 is one function behind the existing cap; T3 and T4 are
independent. Reverting any one leaves the large-file work intact.

## Doc homes when this plan is deleted

- The architecture map → **`docs/CONTEXT_THROUGHPUT.md`** (created).
- The budget rule → `assistant/AGENTS.md` (tool-loop / context-window section).
- What shipped → `docs/RELEASE_NOTES.md`.
