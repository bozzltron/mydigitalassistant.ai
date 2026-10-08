# Experiment: does the context budget earn its place?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The context-throughput work added a per-turn content allowance (T2,
`context_budget.py`) and an aggregate cap that collapses older tool results when
the allowance is spent (T3, `stream_tool_loop`). Both were built to fix a real
failure — a 43k-char CSV filled a 16,384-token window and Ollama truncated it
silently — but the *aggregate* half guards a scenario ("several large tool
results in one turn") that has never been measured.

This measures whether the machinery does work on realistic turns, so the
"is this over-engineered?" question is answered with data. If the aggregate cap
never fires, it is a solution to a problem we do not have, and it should go.

## Hypotheses

- **H1 (safety).** On representative turns, the tool loop's peak prompt stays
  below the window (`context_usage` `truncated == False`).
- **H2 (aggregate — the decisive test).** `tool_results_dropped > 0` on realistic
  multi-tool turns, i.e. the aggregate cap actually fires. **If it is always 0,
  T3's eviction is unjustified.**
- **H3 (binding).** Content results actually reach the allowance (a read or
  listing is capped) rather than sitting comfortably under it — the budget is
  doing work rather than reserving space nothing uses.

## Variables

- **Turn set (fixed):**
  1. read a large CSV and report the total → `read_file` capped.
  2. list every file → `list_files` capped.
  3. read the large CSV, then read it again and compare → two large results in
     one turn (the aggregate scenario).
  4. a turn with a long history → the allowance shrinks.
- **Measured per turn:** `prompt_tokens`, `context_window`, `truncated`,
  `tool_result_chars`, `tool_results_dropped`, and the content allowance.
- **One model**, the warm chat model; `num_ctx = CHAT_NUM_CTX`.

## What counts as evidence

- H2 is the primary test. **`tool_results_dropped == 0` across every turn is a
  negative result: the aggregate eviction is dead weight, and I will say so.**
- H1 is judged against the window, not against a target percentage.
- H3 is judged by whether any content result is capped, not by its size.

## Falsification conditions

1. H2 = 0 across the set → delete T3's eviction; keep only the per-result cap.
2. H1 holds with wide margin **and** H3 never binds → the budget is over-tuned;
   simplify toward a single cap.
3. H1 fails (a turn reaches the window) → the budget is under-tuned; the fix is
   T4 (de-duplicate prose, token-bound history), not more machinery.

## Method

`experiment.py` builds a scratch database and sandbox (never the live brain),
constructs the real tool-loop prompt with the real builtin tools, installs the
budget via `measure_budget` from the measured components, and drives
`stream_tool_loop` with the local Ollama model. It writes `result.json`.

Read-only against the live brain by construction: it opens a scratch DB in a temp
directory and never mounts the live volume, so `preflight.py` (which gates
experiments that open a *brain copy*) does not apply. If it is ever pointed at a
real brain copy, run `preflight.py` first.

```
python assistant/experiments/context_budget_value/experiment.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- Four scripted turns are a probe, not a benchmark; they can kill a hypothesis,
  not certify a design.
- The budget is a chars→tokens estimate; the measured number is Ollama's
  `prompt_eval_count`.
- A single model and a synthetic corpus; production traffic may differ.
- `tool_results_dropped` fires only if the model actually asks for two large
  results in one turn — a model that reads once cannot exercise the aggregate.
  That is the point: if the model does not, the aggregate is unexercised.
