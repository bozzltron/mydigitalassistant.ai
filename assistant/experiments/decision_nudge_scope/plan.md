# Experiment: should the `needs_action` nudge fire on read-class requests?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The `needs_action` nudge (`decision_model_routing`, committed `486b277`) fires
whenever a decision model calls a request `action` and the turn makes no tool call.
Its question is broad: it counts **reads and lookups** as actions.

The design principle says the nudge should enforce **intent** — did the thing the
user asked for happen? — not accuracy. And the asymmetry underneath it: words can be
mistaken for a **write** ("I've created the file" without creating it) but not for a
**read** the model needs (if it lacks the content it cannot answer, so it reads). If
that is right, the nudge should be scoped to **state changes** and never fire on a
request an answer can satisfy.

Two things are unmeasured:

1. Does a **change-scoped** question still catch the write-class failures? (The live
   case is in this set.)
2. Does the **current** question actually fire on read-class requests under the
   deployed model and threshold — or is the annoyance hypothetical?

## Hypotheses

- **H1 (the fix survives).** A change-scoped question classifies the `change` set
  `change` at **≥ 90%** at the deployed action-biased threshold. The live failure
  ("We want to create Friend Music Records Release Template.md") is in this set.
- **H2 (the annoyance goes).** The change-scoped question fires on **0** of the
  `read` + `answer` set.
- **H3 (it is worth doing at all).** The current question fires on **≥ 1**
  read/answer case under the deployed model (`tev1:0.8b`) at the deployed threshold
  (0.4). If it fires on none, the annoyance is hypothetical and the narrowing buys
  nothing — a negative result.

## Variables

- **Decision models:** `tev1:0.8b` (deployed), `tev1`, `nimble` via
  `/v1/systemone`, a `choice` question.
- **Questions:**
  - `needs_action` — the current question (create/read/edit/rename/delete a file;
    run a task; search the web — vs just answering).
  - `needs_change` — the scoped question (create/edit/rename/delete/append/move a
    file or record — vs satisfying the request by telling the user something).
- **Labelled set (fixed, committed with this plan):** 28 requests in four classes —
  - `change` (10): create / write / edit / rename / delete / append / move /
    add-a-map-point. **The nudge must fire.**
  - `read` (8): read a named file, count items in a list, "what did my briefing
    find", list tasks. **The nudge should not fire** (an answer can satisfy it).
  - `answer` (7): facts, memory, explanation, pleasantries. **The nudge should not
    fire.**
  - `search` (3): lookups. **Reported separately** — the router, not this nudge,
    owns forcing search.
- **Measured per request:** the choice, P(positive), and wall-clock. **Firing** =
  P(positive) ≥ 0.4 (the deployed bias), the same rule `decision._yes` uses.

## Target behaviour

**Fire iff the request requires a state change.** T = (`class` == `change`).
Everything else must be silent.

## What counts as evidence

- **`change` recall** decides whether the fix survives.
- **`read` / `answer` firing** is the cost the narrowing removes.
- **`search` firing** is a watch item, not part of the decision.

## Falsification conditions

1. Change-scoped `change` recall **< 80%** → the split breaks the fix; do not ship it.
2. Change-scoped `change` recall **≥ 90%** AND it fires on **0** `read`/`answer` AND
   the current question fires on **≥ 1** `read`/`answer` → ship the narrowing, and
   re-verify the live scenario.
3. The current question fires on **0** `read`/`answer` under the deployed model and
   threshold → the annoyance is **hypothetical**; keep the current question. A
   negative result.

## Method

`experiment.py` calls `/v1/systemone` against local Ollama. No brain, no tools, no
network beyond localhost.

```bash
python assistant/experiments/decision_nudge_scope/experiment.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- **28 requests is a probe**, not a rate.
- **Read labels are arguable.** "Read Why Not Release.md" names a file; a reader
  could argue it *is* an operation. It is labelled `read` because the assistant can
  satisfy it from memory if it holds the content — and that argument is the thing
  under test, so it is named here rather than hidden.
- **Threshold sensitivity.** The deployed config biases toward firing (0.4). Results
  are reported as P(positive) per case, so a threshold change is visible rather than
  hidden behind the choice.
- **One run.** Confidences on this API are low (the routing probe saw 0.01–0.35); a
  near-tie can flip.
- **Classification only.** This measures how each question *classifies*; whether the
  model then performs the action on a read-class turn is a behavioural check on the
  live scenario, not here.
- **`search` is a watch item.** If the change-scoped question drops search entirely,
  that is expected — but whether the router's `wants_search` covers it is a separate
  measurement, flagged, not assumed.
