---
date: 2026-10-07
status: active
estimated_hours: 7
---

# Large files: make the context window visible, budgeted, and pageable

## Objective

The agent must work with large files — reading them, referencing them, and
building them up — **without filling the context window, and without ever
silently answering from a fragment.**

Two outcomes:

1. **Transparency.** The user can see how much of the model's window a turn used,
   and is told when content was dropped.
2. **Agility.** A large file enters the prompt as a *shape* plus a *handle*, never
   as its bytes. The agent reads the piece it needs, appends without reading, and
   checks membership without holding the list.

## The measured failure this fixes

`conv_d2217d74cd4b`, 2026-10-07. The user asked about `subscribers_active.csv`
(626 rows, 43,237 chars) and the agent answered with near-identical summaries four
times (messages 27/29/31/33) — reporting ~150–168 entries each time — despite
different requests.

The app's own logs showed nothing wrong: `context_stats: prompt_chars=11913 ...
truncated=False` (that is the *initial* system prompt only). The Ollama server log
showed the truth:

```
new prompt, n_ctx_slot = 16384, task.n_tokens = 16344
stop processing: n_tokens = 16383, truncated = 1
```

The tool-loop prompt — system prompt (~12k chars) + every tool schema (~13.5k
chars, 17 tools) + the 43k-char CSV + history — reached **~16.3k tokens against a
16,384 window**. llama.cpp truncated it, so the model answered from a fragment (it
reported ~150 entries of the 626) and had no signal that it had. The budget bug is
`MAX_READ_CHARS_FOR_MODEL = 60_000` (~15k tokens): a single read may nearly fill
the window by itself.

## Design principles this must hold

- **The model decides *what*; the code decides *where*.** Membership (is this
  contact already in the file?) is a set check — code, not model recall.
- **Truncation happens on our side of the boundary.** If *we* cap, the marker
  rides in the tool result and the model knows. Ollama's truncation is invisible
  to the model, so it must not be the mechanism.
- **Format-agnostic tools.** No CSV-specific operations. Paging, append, search,
  and the profile work for any text file.
- **Nothing is silently dropped.** A fragment is always labelled, and the label
  says how to get the rest.

## Phases

### Phase 1 — Instrument: measure the real prompt *(for the user)*

Today we log only the initial prompt. Capture what Ollama actually evaluated.

- `pipeline/llm_client.py`: parse `prompt_eval_count` and `eval_count` from the
  Ollama response into `ChatResponse` (alongside the existing `done_reason`).
- `pipeline/streaming.py` (`stream_tool_loop`): track the **maximum**
  `prompt_eval_count` across the loop's turns — the true peak, not turn 1.
- `pipeline/orchestrator.py`: log it with the window (`max_prompt_tokens`,
  `num_ctx`, `pct`, `truncated`) and carry it on the turn's `meta`.
- Frontend: the trace panel shows a **context meter**.

### Phase 2 — Budget the read; make the marker actionable *(for the agent)*

This is the phase that answers "does the agent know it was truncated?".

- Derive the read cap from the window: `chat_num_ctx` minus the fixed cost
  (system prompt + tool schemas + history) minus headroom for the answer, rather
  than the fixed `MAX_READ_CHARS_FOR_MODEL`.
- `_bounded_for_model` (`pipeline/tool_executor.py`): use the derived cap, and
  replace the current marker (`... [truncated for this turn: N characters total]`)
  with an **actionable** one that names the range and the paging handle:
  `showing lines 1–150 of 628; call read_file(path="…", offset=150) for the next page`.
- **Backstop:** detect post-hoc truncation (`prompt_eval_count` at/near
  `num_ctx`) and surface it; if it happens, the residual fixed cost filled the
  window and the marker was lost — compact-and-retry (the `compact_messages` path
  that today only fires on an *empty* generation) and note it.

### Phase 3 — Paging

`read_file(path, offset=0, limit=None)`, in **lines**, returning the range and the
totals so the model knows where it is:

```
lines 201–250 of 628 (chars 13,940–18,210 of 43,237)
```

Generic for any text file (CSV, JSONL, logs, markdown, code). This is the escape
hatch the Phase 2 marker points at.

### Phase 4 — Generic append and search

Replace the (rejected) CSV-centric `add_rows` with two format-agnostic tools:

- **`append_file(path, content)`** — build a file without reading it.
- **`search_file(path, query)`** — matching lines, for membership ("is this email
  already here?") and targeted lookup.

Dedup becomes composition (`search_file` → `append_file`), with no format
assumption. If an airtight guarantee is wanted later, it is a generic option
(`append_file(..., unique_lines=True)`), still not CSV-aware.

### Phase 5 — Generic file profile

Compute a profile at upload/write time and store it on the file frame, so "what
is this file and where are the gaps?" is a **memory read**, not a file read.

- CSV: columns, row count, per-column distinct counts, category/geo distribution.
- JSON: keys, array lengths.
- Markdown: headings. Plain text: line/word counts.
- Surfaced in `format_memory_context` (`memory/retrieval.py`).

This is what the model kept trying to hand-roll ("~92% radio, ~8% venue") and
getting wrong. It is deterministic, so it should be computed, not hallucinated —
and it doubles as the search steer for gap-finding tasks.

## Key decisions (recorded so they are not relitigated)

1. **Show a percentage of the window, with tokens as the detail** (`97% · 15.9k/16.4k`).
2. **Truncation is ours (budgeted).** The marker is always in the tool result;
   Ollama truncation is a detected backstop, not the mechanism.
3. **Tools stay format-agnostic.** No `add_rows`; paging/append/search/profile.
4. **The profile is a generic "what is this file"** — each extractor fills what it can.
5. **The budget is derived, not a constant.** `MAX_READ_CHARS_FOR_MODEL` stops being the whole story.

## Test strategy

- **Unit:** the read-cap derivation; the marker text (range + paging handle);
  paging offsets and range reporting; `append_file` / `search_file`;
  the profile per format.
- **Regression (the point):** reproduce this exact CSV turn — a file larger than
  the window must produce a **marked, actionable** truncation, never a silent
  one. Pin the marker's presence in the tool result.
- **Agent honesty:** with a stubbed LLM, assert the truncation marker reaches the
  model's messages (so it *can* relay it).
- **API/meta:** the `context` object is on the turn's `meta` with the right pct.

## How we will know it worked

- The CSV turn's prompt never exceeds the window without the agent holding a
  marker that names the range and the next page.
- `turn_pregen`/`turn_timings` (or the new line) reports the true peak prompt and
  the window, and the trace shows the meter.
- Re-running the `subscribers_active.csv` task: the agent either pages to the rows
  it needs or says plainly it saw a fragment — it does not repeat a wrong count.

## Rollback

Each phase is additive and independently shippable: the token capture, the cap
derivation, paging, append/search, and the profile are separate changes. Reverting
any one leaves the others working; the cap change is a one-line default if it
misbehaves.

## Threats and limits

- **We do not tokenize locally**, so the cap derivation is a chars/token estimate.
  Measure the estimate against `prompt_eval_count` before trusting the budget.
- **The fixed cost varies** (tool schemas grow; history length varies), so the
  budget must be computed per turn, not cached.
- **Ollama's truncation semantics** (which tokens it drops) are not ours to
  control; the backstop detects that it happened, not what was lost.
- **`compact_messages` drops the tool result** — if the backstop fires it may
  remove the very content under discussion; the note must say so.

## Out of scope

- A headless browser for JS-rendered pages.
- Changing `chat_num_ctx` or the model — that is a fleet/config decision
  (`docs/MODEL_SELECTION.md`), not this work.
- Vector search over file contents.

## Doc homes when this plan is deleted

- The rule "a tool result must not fill the window; a capped read is always
  marked" → `assistant/AGENTS.md` (tool loop / context window section).
- The tools and their contracts → `docs/FILES.md`.
- What shipped → `docs/RELEASE_NOTES.md`.
