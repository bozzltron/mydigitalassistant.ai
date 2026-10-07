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
| Conversation history | **6 prior turns, verbatim** (not tokens) | `orchestrator._run_turn` |

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

The system prompt also lists the file tools **in prose** (`llm_client.py`), the
same information the JSON schemas carry — a known duplication (see Bottlenecks).

Tool schemas are added by Ollama from the `tools` argument, not from
`system_prompt`, so the 12,000-char cap does **not** see them.

## 5. The tool loop

`stream_tool_loop` (`streaming.py`), `MAX_TOOL_ROUNDS = 3` (`tools.py`). Each
round's prompt is `system + every tool schema + history + user + all tool results
so far`; tool results **accumulate**.

| Per-result cap | Chars | ≈Tokens | Home |
|---|---|---|---|
| `read_file` | 22,936 | 5,734 | `_read_char_limit()` = `num_ctx × 0.35 × 4` |
| `search_file` | ≤80 × 200 = 16,000 | 4,000 | `MAX_SEARCH_MATCHES` |
| `fetch_url` | 3,000 / page (up to 5 pages) | 750–3,750 | `_snippet()` |
| `list_files` / `glob` | sandbox entries ≤1,000; **no char cap** | up to ~20k | `filesystem.MAX_GLOB_RESULTS` |
| Output | **none** unless thinking → 4,096 | — | `think_num_predict_cap` |

`list_files` also enriches from memory frames, which is **not** capped by
`MAX_GLOB_RESULTS`.

## 6. Output

The tool path calls blocking `chat()`, so the answer arrives whole (no text
streaming) and `ttft ≈ total`. The answer is persisted as an episode — which is
**unbounded** and becomes the next turn's history.

## 7. The token arithmetic (the hose, measured)

Against the 16,384 window, turn 1 of the tool loop:

```
tool schemas            4,444   (27%)
system prompt (≤)       3,000   (18%)
------------------------------
fixed cost              7,444   (45%)  before a single fact, file, or word
remaining               8,940
one capped read         5,734   (64% of what's left)
```

The per-item caps **do not compose**: each was sized against the whole window,
not against a shared remainder. One read plus the fixed cost leaves ~3.2k tokens
for history and the answer; add a `search_file` or a `list_files` and the
remainder is gone.

## 8. How it is measured

One greppable INFO line per turn, plus the peak:

- `turn_pregen:` — the dead time in front of the first token (routing, recall,
  plan, extraction, search).
- `turn_timings:` — total, per phase, and `ttft_ms` (streaming path only).
- `context_usage:` — the tool loop's **peak prompt tokens** and the window, with
  `pct` and `truncated` (the prompt reached the window).
- `context_fixed:` — the composition in chars: `system_prompt_chars`,
  `tool_schema_chars`, `history_chars`, `tool_result_chars`, with the peak tokens.

`prompt_eval_count` from Ollama is the only true token count; every char figure
here is a ÷4 estimate, and JSON tool schemas tokenize worse than that.

## 9. Known bottlenecks

1. **The read cap is a fraction, not a derivation.** `_read_char_limit()` uses
   `× 0.35` rather than `num_ctx − fixed cost − headroom`, so the reserve it
   implies is fictional once the fixed cost is 7.4k tokens.
2. **Tool results are capped individually, never in aggregate** across the 3
   rounds.
3. **`list_files` / `glob` have no character cap**; one call can exceed the window.
4. **History is bounded by turns, not tokens** — one long prior answer is re-sent
   every subsequent turn.
5. **Tool schemas are the largest fixed cost, and the file tools are duplicated**
   in the system prompt prose.
6. **`chars_per_token = 4` is optimistic** for JSON.
7. **Extraction runs on a 4,096 window** with an unbounded user message.
8. **No output reservation** — generation competes with content.

## 10. Where each cap lives

- Windows: `assistant/backend/config.py` (`*_num_ctx`), resolved by
  `OllamaClient._num_ctx_for`.
- System prompt budget: `max_system_prompt_chars`; fitting in
  `Orchestrator._memory_char_budget` / `_fit_prompt_to_cap`.
- Read/search caps: `tool_executor.py` (`_read_char_limit`, `MAX_SEARCH_MATCHES`).
- Fetch cap: `tools.py` (`_snippet`).
- Loop length: `tools.py` (`MAX_TOOL_ROUNDS`).
- Glob cap: `filesystem.py` (`MAX_GLOB_RESULTS`).

## Related

- The large-file work (visible window, budgeted read with an actionable marker,
  line paging, append/search, the file profile, precise editing) is documented in
  `docs/FILES.md` and `assistant/AGENTS.md`.
