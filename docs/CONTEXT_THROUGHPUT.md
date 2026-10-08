# Context throughput — how the hose is sized, end to end

How a turn's context is assembled, which model window each call runs in, where
every cap is, and how it is measured. This is the reference for "why did the
prompt get so big?" and for any change to a context budget.

The guiding rule: **the model's context window is the whole turn's budget**, and
the tool loop is the largest prompt in the system (system prompt + every tool
schema + history + tool results). Everything else is sized so that prompt can
still answer.

## 1. The flow

```
user message ─┐
              ├─► route ─┐
              │          ├─► recall (embed) ─┐
              │          │                   ├─► plan ─┐
              │          └─► extraction ─────┘         │
              │                                       ▼
              └─► search (optional, external) ─► PROMPT ASSEMBLY
                                                      │
                              system prompt + tool schemas + history + user
                                                      │
                                                      ▼
                                             stream_tool_loop  (≤3 rounds)
                                                      │
                                                      ▼
                                                   answer
```

Pre-generation is a sequence of **separate LLM calls, each with its own window**
(routing/plan/extraction on the utility model; recall on the embedder). They do
not share the tool loop's window — but they do add latency in front of the first
token. The tool loop is the only place context *accumulates* within a turn.

## 2. Input — what enters

| Path | Cap | Home |
|---|---|---|
| `ChatRequest.message` | **none** | `orchestrator.py` |
| Attached files / uploads | **none** (removed on purpose) | `docs/FILES.md` |
| Pasted content (`register_user_content`) | **none** — stored, then injected whole | `orchestrator._render_supplied_content` |
| Conversation history | the last `verbatim_history_turns` (6) turns, and bounded by `history_char_limit` (25% of the window) | `orchestrator._bounded_history` |

The input has no width limit; every bound is downstream.

## 3. Per-model context windows (`num_ctx`)

Resolved in one place — `OllamaClient._num_ctx_for` (`llm_client.py`):

| Role | Default model | `num_ctx` | Residency |
|---|---|---|---|
| Chat | `qwen3.5:9b` | 16,384 | warm (`-1`) |
| Tools | `qwen3.5:9b` (**shares chat**) | 16,384 (the chat window) | warm |
| Utility | `qwen3.5:4b` | 4,096 | warm |
| Embedding | `qwen3-embedding:0.6b` | — | warm |
| Max | empty → chat | 16,384 | on-demand `10m` |
| Math | empty → off | 16,384 | on-demand `10m` |

Because `tools_model == chat_model`, **one runner and one window serve the whole
turn**: tool calls and the final answer share the 16,384 that the tool loop
overflows first. `tools_num_ctx` applies only to a *distinct* tools model; do not
reintroduce per-call-site `num_ctx` branches.

## 4. Prompt composition — the fixed cost

Measured in this image (2026-10-07):

| Component | Chars | ≈Tokens (÷4) | Notes |
|---|---|---|---|
| **Tool schemas** | **17,778** | **4,444** | 19 tools, sent on **every** tool-loop round |
| System prompt cap | 12,000 | 3,000 | `max_system_prompt_chars` |
| — measured overhead | 3,118 / 2,378 | 780 / 595 | functional / introspective, before memory |
| Memory section | 12,000 − overhead − appends | — | ≤10 frames, ≤10 episodes, digests ≤240 chars |
| Search results | ≤3 results | ~0.5k | `max_search_results_in_prompt` |

The system prompt no longer duplicates the tool schemas: the bulleted prose list
named only eight of the nineteen tools and repeated the JSON schemas, which are
the real contract. The chaining examples and the uploaded-file note remain,
because the schemas do not carry them.

Tool schemas are added by Ollama from the `tools` argument, not from
`system_prompt`, so the 12,000-char cap does **not** see them.

## 5. The tool loop

`stream_tool_loop` (`streaming.py`), `max_tool_rounds` (`config.py`; 6 base, 12
deep). Each
round's prompt is `system + every tool schema + history + user + all tool results
so far`; tool results **accumulate**.

Every content-carrying result is capped by the **turn's content allowance** —
`window − measured fixed cost − reserved answer` — derived per turn in
`context_budget.py` (see §7). It adapts: a longer history or a bigger tool set
shrinks it.

| Per-result cap | Cap | Home |
|---|---|---|
| `read_file` | the turn's content allowance | `_read_char_limit()` |
| `search_file` | ≤80 lines **and** ≤ the allowance | `MAX_SEARCH_MATCHES` |
| `fetch_url` | `min(3,000, allowance)` / page (up to 5 pages) | `_snippet()` |
| `list_files` / `glob` | ≤1,000 entries **and** ≤ the allowance; `count` stays the true total | `MAX_GLOB_RESULTS` |
| Output | **none** unless thinking → 4,096 | `think_num_predict_cap` |

`list_files` also enriches from memory frames; the trim keeps at least one entry
and marks the result truncated so the model knows the list is partial.

**The aggregate is capped too.** The rounds share one allowance: when a new tool
result would exceed it, the oldest results are collapsed to `DROPPED_TOOL_RESULT`
— a marker the model sees, and one that keeps the `tool_call`/`tool_result`
pairing intact — so a long tool chain cannot overflow the window round over
round. The count is logged as `tool_results_dropped`.

The held total is therefore bounded at the allowance **plus one marker per dropped
result** (104 chars each; ≤ ~600 with the round cap). The marker is itself
content the model sees, and the answer reserve absorbs it — the bound is the
allowance plus a few hundred chars, not the allowance exactly.

**Loop length follows the task.** `max_tool_rounds` (default 6) is a *runaway
guard*, not a task budget: the model ends the loop itself with a direct answer or
a `finalize` call. A deep task — the reasoner's `think` / `max_intelligence`
escalation — gets `max_tool_rounds_deep` (default 12). Because the aggregate is
bounded, more rounds cost latency only, not context.

## 6. Output

The tool path calls blocking `chat()`, so the answer arrives whole (no text
streaming) and `ttft ≈ total`. The answer is persisted as an episode — which is
**unbounded** and becomes the next turn's history.

## 7. The token arithmetic (the hose, measured)

Against the 16,384 window, turn 1 of the tool loop:

```
tool schemas            4,444   (27%)
system prompt (≤)       3,000   (18%)
history + user turn     (varies)
------------------------------
fixed cost             ~7,444   (45%)  before a single fact, file, or word
reserved answer         4,096   (25%)  think_num_predict_cap
------------------------------
content allowance      ~4,844   (30%)  what a read/search/listing may use
```

The allowance is **derived**, not a fraction: `window − fixed cost − reserved
answer`. It shrinks as history or the tool set grows, and grows when they are
small. The fixed cost is measured per turn and logged as `context_fixed`. The
per-item caps now all draw from this one allowance; the sum across the loop's
rounds is still bounded separately (see Bottlenecks).

## 8. How it is measured

One greppable INFO line per turn, plus the peak:

- `turn_pregen:` — the dead time in front of the first token (routing, recall,
  plan, extraction, search).
- `turn_timings:` — total, per phase, and `ttft_ms` (streaming path only).
- `context_usage:` — the tool loop's **peak prompt tokens** and the window, with
  `pct` and `truncated` (the prompt reached the window).
- `context_fixed:` — the composition in chars: `system_prompt_chars`,
  `tool_schema_chars`, `history_chars`, `tool_result_chars`,
  `tool_results_dropped`, with the peak tokens.
- The per-turn content allowance itself (`context_budget.py`):
  `window − fixed cost − reserved answer`, installed by `_run_turn` and read by
  every content-carrying tool while the loop runs.

`prompt_eval_count` from Ollama is the only true token count; every char figure
here is a ÷4 estimate, and JSON tool schemas tokenize worse than that.

## 9. Bottlenecks

Resolved by T2 (the per-turn budget, `context_budget.py`):

- ~~The read cap was a flat fraction~~ — now derived from the measured fixed cost
  (`window − fixed − reserved answer`), and it adapts to history and tool size.
- ~~`list_files` / `glob` had no character cap~~ — now trimmed to the allowance.
- ~~No output reservation~~ — the answer now has an explicit reserve
  (`RESERVED_OUTPUT_TOKENS`, matching the thinking cap).

Resolved by T3 (the aggregate cap, `stream_tool_loop`):

- ~~Tool results were capped individually, never in aggregate~~ — the rounds now
  share one allowance; older results collapse to a marker when it is spent.

Resolved by T4:

- ~~History is bounded by turns, not tokens~~ — `_bounded_history` bounds it by
  size too, and marks a single oversized turn.
- ~~The file tools are duplicated in the system prompt prose~~ — the stale prose
  list is gone; the schemas are the contract.

Still open:

1. **`chars_per_token = 4` is optimistic** for JSON, so the fixed cost may be
   under-counted and the allowance over-stated; `context_usage` is the check.
2. **Extraction runs on a 4,096 window** with an unbounded user message.

## 10. Where each cap lives

- Windows: `assistant/backend/config.py` (`*_num_ctx`), resolved by
  `OllamaClient._num_ctx_for`.
- System prompt budget: `max_system_prompt_chars`; fitting in
  `Orchestrator._memory_char_budget` / `_fit_prompt_to_cap`.
- Read/search/listing caps: `tool_executor.py` (`_read_char_limit`,
  `_cap_entries_to_budget`, `MAX_SEARCH_MATCHES`).
- The per-turn content allowance: `pipeline/context_budget.py`
  (`TurnBudget`, `content_char_limit`) and the history bound
  (`history_char_limit`), both read by `Orchestrator._run_turn`.
- Fetch cap: `tools.py` (`_snippet`).
- Loop length: `config.py` (`max_tool_rounds`, `max_tool_rounds_deep`).
- Glob cap: `filesystem.py` (`MAX_GLOB_RESULTS`).

## Related

- The large-file work (visible window, budgeted read with an actionable marker,
  line paging, append/search, the file profile, precise editing) is documented in
  `docs/FILES.md` and `assistant/AGENTS.md`.
