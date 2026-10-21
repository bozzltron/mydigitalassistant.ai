# Experiment: should a confident "needs current info" outrank sufficient memory?

**Status:** pre-registered before data collection. `result.md` is written only
after `verification.md`.

## What the live check found

Three live turns asking "What is NASA's image of the day?": the first searched and
was right; the second and third answered from the previous assistant **episode**
without searching. The reason is not a prompt: `assess_memory_sufficiency`
(`reasoner.py:147`) is a heuristic — a frame with relevance ≥ 0.3 and average
confidence ≥ 0.6 counts as `HIGH`, and `plan.search_needed` is only set when
sufficiency is `NONE`.

The router's model judgement (`wants_search`) is then consulted
(`orchestrator.py:1567-1591`) and can only:

- **veto** a search (`wants_search is False`), or
- **force** one when sufficiency is `NONE` (`wants_search is True`).

So when memory holds a plausible frame, the model's "this needs current
information" is discarded. For a period-scoped question that is exactly backwards:
the memory *is* relevant, and it is *stale*.

## Hypothesis

Removing the `sufficiency == NONE` condition from the router-positive branch raises
search on period-scoped questions, which are precisely the ones whose stored
answer expires, without pulling stable questions into search.

This is model-first: the decision is already the router's judgement (`wants_search`,
measured at 0.90 recall on period phrases in `temporal_validity`). The change
removes a hardcoded override that outranks it — it does not add a phrase list.

## Variables

**Independent — the gate**, two policies applied to the same recorded router
judgements:

| policy | search when |
|---|---|
| `baseline` | sufficiency `NONE`, minus the router veto, plus the existing `NONE` force |
| `router_positive` | `wants_search is True` **regardless of sufficiency**, minus the veto |

**Dependent:** search rate per query group.

**Query groups** (20, from `temporal_validity`):

- `period` (10) — the answer changes with the date: image of the day, prices,
  weather, scores, "latest".
- `stable` (10) — definitions, the user's own stored facts, storage-style turns.
  This is the **over-search control**: a policy that searches for everything has
  not improved anything.

**Memory condition (the point of the experiment):** every query is evaluated
against a *sufficient* memory context — one frame at relevance 0.85, confidence
0.8, which `assess_memory_sufficiency` calls `HIGH`. That is the live failure:
relevant memory present, and stale.

Sufficiency is computed by calling the real `assess_memory_sufficiency`, not
assumed, and the harness asserts it returns `HIGH` for every query.

## Controls

- Router judgement recorded **once per query** (`classify_with_llm`, utility
  model, temperature 0.0) and reused for both policies, so the only difference is
  the gate.
- Same memory context for every query and both policies.
- No search is executed and no chat generation runs: the measurement is the
  decision function, so there is no internet variance and no model sampling.
- Three replicates of the router call to expose any label flapping.

## Falsification (decided before the run)

- **Null** if `router_positive` raises the period search rate by < 0.30 absolute.
- **Over-search** if it raises the stable search rate by > 0.10. A policy that
  searches stable questions is not a fix; it is a latency and quota regression.
- **Ceiling** if `baseline` already searches ≥ 0.80 of period queries **with
  sufficient memory** — impossible by construction (sufficiency HIGH suppresses
  it), so if this happens the harness is not modelling the code and the run is
  void.

## What this does not measure

- **Latency and quota cost** of the extra searches. The gate's cost is the search
  count it adds; `google/bing`-class latency is already measured per turn, but this
  run does not time it.
- **Answer quality** after searching. A search that returns nothing useful still
  costs the round trip. The follow-up is the six-question live set.
- **The router's recall on period phrasing** — already measured (0.90) in
  `temporal_validity`; this run re-checks it on the same set as a sanity control,
  and a drop below 0.80 voids H1 rather than supporting it.

## Success criteria

- [ ] `assess_memory_sufficiency` returns `HIGH` for all 20 queries (asserted)
- [ ] `baseline` period search rate ≈ 0 with sufficient memory (the bug,
      reproduced)
- [ ] `router_positive` period search rate ≥ baseline + 0.30
- [ ] `router_positive` stable search rate ≤ baseline + 0.10
- [ ] Router judgements stable across 3 replicates, or the flapping is reported
- [ ] No database, no search, no generation

## Run

```bash
docker run --rm -i -v "$(pwd):/app" -w /app \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  mydigitalassistantai-assistant python -m assistant.experiments.temporal_search_gate
```
