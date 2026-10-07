---
date: 2026-10-07
status: active
estimated_hours: 8
---

# Context throughput: how wide is the hose, end to end

## Objective

Map every cap and buffer from the user's keystroke to the model's answer, so the
question "is this throughput optimized for the project's goals?" is answered with
numbers rather than a feeling. Not a rewrite: a **map**, a **bottleneck
inventory**, and a **decision**.

The goals it is measured against: privacy-first local inference; household scale
(a person, not a corpus); a fast first token; and the rule that the agent must
never silently answer from a fragment.

## The map

### 1. Input — what enters the system

| Path | Cap | Home |
|---|---|---|
| `ChatRequest.message` | **none** | `orchestrator.py:119` |
| Attached files / uploads | **none** (removed on purpose) | `docs/FILES.md` |
| Pasted content (`register_user_content`) | **none** — stored, then injected whole | `orchestrator._render_supplied_content` |
| Conversation history | **6 prior turns, verbatim** (not tokens) | `orchestrator.py:1678-1685` |

So the *input* has no width limit at all. Everything is bounded downstream.

### 2. Per-model context windows (`num_ctx`)

Resolved in one place, `OllamaClient._num_ctx_for` (`llm_client.py:217`):

| Role | Default model | `num_ctx` | Residency |
|---|---|---|---|
| Chat | `qwen3.5:9b` | 16,384 | warm (`-1`) |
| Tools | `qwen3.5:9b` (**shares chat**) | 16,384 (the chat window) | warm |
| Utility | `qwen3.5:4b` | 4,096 | warm |
| Embedding | `qwen3-embedding:0.6b` | — | warm |
| Max | empty → chat | 16,384 | on-demand `10m` |
| Math | empty → off | 16,384 | on-demand `10m` |

Because `tools_model == chat_model`, **one runner and one window serve the whole
turn** — tool calls and the final answer share the 16,384 the tool loop overflows
first. `tools_num_ctx` only applies to a *distinct* tools model.

### 3. Prompt assembly — the fixed cost before any content

Measured in this image (2026-10-07):

| Component | Chars | ≈Tokens (÷4) | Notes |
|---|---|---|---|
| **Tool schemas** | **17,778** | **4,444** | 19 tools, sent on **every** tool-loop round |
| System prompt cap | 12,000 | 3,000 | `max_system_prompt_chars` |
| — measured overhead | 3,118 / 2,378 | 780 / 595 | functional / introspective, before memory |
| Memory section | 12,000 − overhead − appends | — | ≤10 frames, ≤10 episodes, digests ≤240 chars |
| Search results | ≤3 results | ~0.5k | `max_search_results_in_prompt` |

Note the system prompt **also lists the file tools in prose** (`llm_client.py:855-877`)
— the same information the JSON schemas carry.

### 4. The tool loop — where content actually flows

`stream_tool_loop` (`streaming.py:185`), `MAX_TOOL_ROUNDS = 3`.

| Per-result cap | Chars | ≈Tokens | Home |
|---|---|---|---|
| `read_file` | **22,936** | **5,734** | `_read_char_limit()` = `num_ctx × 0.35 × 4` |
| `search_file` | ≤80 × 200 = 16,000 | 4,000 | `MAX_SEARCH_MATCHES` |
| `fetch_url` | 3,000 / page (+ up to 5 pages) | 750–3,750 | `_snippet()` |
| `list_files` / `glob` | ≤1,000 entries, **no char cap** | up to ~20k | `sandbox_max_glob_results` |
| Output | **none** unless thinking → 4,096 | — | `think_num_predict_cap` |

Each round's prompt is `system + every tool schema + history + user + all tool
results so far`. Tool results **accumulate** across rounds.

### 5. Output

The tool path calls blocking `chat()`, so the answer arrives whole (no text
streaming); `ttft ≈ total` there. The answer is persisted as an episode — which
is *unbounded* and becomes the next turn's history.

## The token arithmetic (the actual hose)

Against the 16,384 window, turn 1 of the tool loop:

```
tool schemas            4,444   (27%)
system prompt (≤)       3,000   (18%)
------------------------------
fixed cost              7,444   (45%)  <- before a single fact, file, or word
remaining               8,940
one capped read         5,734   (64% of what's left)
```

Add one `search_file` (4,000) or one `list_files` (up to ~20k) and the remaining
budget is gone — **the per-item caps do not compose**, because each was sized
against the whole window rather than against a shared remainder.

## Bottlenecks, ranked

1. **The read cap is a fraction, not a derivation.** Phase 2 of
   `plans/2026-10-07-large-file-context.md` specified "`chat_num_ctx` minus the
   fixed cost minus headroom"; the shipped `_read_char_limit()` uses a flat
   `× 0.35`. With a measured 7,444-token fixed cost, the "reserve" it implies is
   fictional — one max read plus the fixed cost leaves ~3.2k tokens for history
   and the answer.
2. **Tool results are capped individually, never in aggregate.** 3 rounds can each
   add a read-sized result; nothing bounds the sum.
3. **`list_files` / `glob` have no character cap.** 1,000 entries is one tool
   result that can, by itself, exceed the window.
4. **History is bounded by turns, not tokens.** Six verbatim turns; one long
   prior answer is re-sent on every subsequent turn.
5. **The tool schemas are the single largest fixed cost, and the file tools are
   duplicated** — once as prose in the system prompt, once as JSON.
6. **`chars_per_token = 4` is optimistic** for JSON/tool schemas, so the real
   prompt is likely larger than budgeted; the only true measurement is Ollama's
   `prompt_eval_count` (now logged as `context_usage`).
7. **Extraction runs on a 4,096 window** (`utility_num_ctx`) with an unbounded
   user message — a long paste is truncated there, silently.
8. **No output reservation.** Generation competes with content for the same
   window, so a near-full prompt is truncated mid-answer.

## Is this optimized for the goals?

**Safe, not yet optimized.** The large-file work made the window *visible*,
*marked*, and *pageable* — no fragment is dropped silently any more. But the
throughput design is still per-item fractions over a shared window, and the
fixed cost is large and partly duplicated. The cheapest, highest-leverage work is
not a bigger model:

- **A — one per-turn budget object.** Measure the fixed cost (system prompt +
  tool schemas + a reserved answer) and split the *remainder* across reads,
  search, and listings, replacing the independent fractions. This is what Phase 2
  of the large-file plan actually specified.
- **B — an aggregate tool-result cap** across the loop's rounds, not just per
  result.
- **C — de-duplicate the prose tool list** (the schemas already carry it).
- **D — bound history by tokens, not turns.**
- **E — cap `list_files` / `glob` output by characters.**

A larger-context model (the candidate in
`assistant/experiments/model_swap_qwen35_claude/` advertises 262,144) changes the
arithmetic but **not the design** — it buys headroom for the same unfixed
composability. Measure it, do not buy it *instead of* A–E.

## Phases (proposed)

1. **Instrument (0.5d).** Make `context_usage` also record the fixed cost
   (system-prompt chars, tool-schema chars) so the split is visible per turn, not
   inferred. No behavior change.
2. **Budget object (1d).** Introduce `TurnBudget` deriving the content allowance
   from the measured fixed cost; wire `_read_char_limit`, `search_file`, and the
   listing tools to it. Keep the marker behavior from the large-file work.
3. **Aggregate tool-result cap (0.5d).** Track spent tool-result chars across the
   loop's rounds and shrink later results, with the same actionable marker.
4. **De-duplicate + history (1d).** Trim the prose tool list; bound history by an
   estimated token budget rather than 6 turns.
5. **Re-measure (0.5d).** Re-run the `subscribers_active.csv` turn and the
   `context_usage` line; confirm the peak prompt drops and no turn reaches the
   window.

## Test strategy

- **Unit:** the budget split (fixed cost → content allowance); the aggregate cap;
  the history token bound.
- **Regression (the point):** the 43k-char CSV turn never reaches the window and
  always carries a marker naming the next page.
- **Measurement:** `context_usage` peak and `pct` before/after, on a fixed turn
  set; a documented before/after in this plan's `result` section.

## How we will know it worked

- The tool loop's peak prompt (`context_usage`) stays below the window with
  headroom on the fixed turn set, and `truncated` is never `True`.
- A `list_files` on a full sandbox cannot, by itself, approach the window.
- The 43k-char CSV turn either pages to what it needs or says plainly it saw a
  fragment — and the peak prompt is lower than today's.

## Threats and limits

- **We do not tokenize locally.** Every chars→tokens figure here is a ÷4 estimate;
  JSON schemas tokenize worse. The real number is `prompt_eval_count`.
- **A bigger model hides, not fixes, composability.** 262k context would let the
  same unbudgeted sums fit — until they do not.
- **Trimming the prose tool list risks the model not knowing a tool exists**; the
  JSON schema is the contract, but small models lean on prose. Measure, do not
  assume.
- **This is a map, not a mandate.** A–E are candidates; the measurement decides
  which earn their place.

## Rollback

Each phase is additive: the instrumentation changes no behavior; the budget
object is one function behind the existing cap; the aggregate cap and history
bound are independent. Reverting any one leaves the large-file work intact.

## Doc homes when this plan is deleted

- The throughput map and the budget rule → `assistant/AGENTS.md`
  (tool-loop / context-window section) and `docs/FILES.md`.
- What shipped → `docs/RELEASE_NOTES.md`.
