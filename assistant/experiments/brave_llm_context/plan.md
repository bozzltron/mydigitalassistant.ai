# Experiment: does Brave's LLM Context endpoint beat our snippet + self-fetch path?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

Brave's own docs say the Web Search endpoint (`/res/v1/web/search`) is *"intended
for human consumption"* and that agents should use the **LLM Context endpoint**
(`/res/v1/llm/context`), which returns pre-extracted, relevance-ranked content
chunks per URL (text, tables, code) instead of one `description`.

We currently do: `/web/search` (with `extra_snippets=true`, Phase 1) → put the top
3 snippets in the prompt → **fetch the top 3 pages ourselves** and extract from
full content. This measures whether switching the *content* channel to LLM Context
extracts more and better facts — before we build it.

## Hypotheses

- **H1 (yield — the decisive one).** LLM Context raises the number of
  **accurate** slots extracted per search turn versus the current path.
- **H2 (corroboration).** It raises the share of extracted slots with **≥2
  independent `source_domains`** (single-source facts are claims, not knowledge).
- **H3 (budget).** It fits the derived content allowance — `maximum_number_of_tokens`
  / `maximum_number_of_urls` set so the extraction input stays bounded and the
  chat prompt does not shrink memory (`_fit_prompt_to_cap`).
- **H4 (latency).** It is **not slower** than the current path, because it removes
  our own outbound page fetches (replaced by one Brave call).

## Variables

- **Arms (same query set, same models, same scratch store):**
  - **A — current:** `/web/search` + `extra_snippets` + self-fetch of top pages.
  - **B — LLM Context:** `/res/v1/llm/context`, token-budgeted; `grounding.generic[]`
    replaces the self-fetch.
- **Query set:** the search queries that actually ran (`episodes.search_info.query`)
  over a window on a read-only brain copy, deduped and sampled across task classes
  (deals/freshness, stable knowledge, recommendation).
- **Metrics:**
  - **M1 — accurate slots/turn** (ground-truth audited on a sample; the utility
    model's own output is not the judge).
  - **M2 — ≥2-domain corroborated share.**
  - **M3 — extraction-input tokens** and the prompt's `context_usage`/`truncated`.
  - **M4 — wall-clock per turn**, split into search vs fetch vs extraction.
  - **M5 — Brave requests spent** (LLM Context is included in the Search plan but
    counts against the same pool).

## What counts as evidence

- **M1 decides.** M2 is the quality guard: more slots that are single-source is not
  a win. M3 is the veto: if B does not fit the budget, it does not ship regardless
  of M1. M4 must not regress materially.

## The bar, stated in advance

- **Ship B if:** M1 improves by **≥ 25%** accurate slots/turn **and** M2 does not
  fall **and** M3 stays within the allowance **and** M4 is not slower by more than
  ~10%.
- **Otherwise:** keep A. A negative result is a result — it saves the change.

## Falsification conditions

1. M1 improvement **< 10%** → the endpoint does not earn its request; do not build.
2. M1 improves but M2 falls → it buys volume at the cost of trustworthiness; do not
   build.
3. M3 exceeds the allowance at any `maximum_number_of_tokens` that preserves M1 →
   it cannot ship as a drop-in; report and stop.
4. M4 slower by **> 25%** → the latency is not worth the yield.

## Threats (named before the run)

- **Quota.** The experiment spends real Brave requests; run it on a capped query
  set and count M5 so the cost is explicit.
- **Ground truth.** M1/M2 need a hand-audited sample; the extractor's own
  confidence is not evidence.
- **The fetched pages are a confound.** Arm A's self-fetch may hit pages B never
  sees (paywalls, JS-rendered); B's chunks come from Brave's index. Attribute
  differences accordingly.
- **One model.** Extraction is the utility model; a different extractor could move
  M1. State the model and hold it fixed.
- **Prompt vs extraction channel.** This experiment measures the *content* channel
  (extraction). Whether LLM Context also improves the *answer* via the prompt is a
  separate question, deferred.

## Method

Read-only against a brain copy for the query set; `preflight.py` gates it. The two
arms run against a scratch store; nothing touches the live brain.

```
EXP_DB=/exp/assistant.db python assistant/experiments/brave_llm_context/experiment.py
```

`result.md` is written only after `verification.md`. The harness (`experiment.py`)
is written when the run is scheduled.
