# Experiment: can a decision model judge `needs_action`?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

A live session could not get a file written: asked for a release template, the
agent **announced** it ("I'll now create…") and **asked a clarifying question**
instead of calling `write_file` — three times. The proposed fix is model-first: a
**model judges whether the request needs an action**, and when the turn ends with
**no tool call** the loop nudges the model once to perform it. No regex, no
phrasing detection.

That design rests on one claim we have not measured: **can a local decision model
(ollama 0.35 `/v1/systemone`) classify `needs_action` accurately and fast enough to
gate that nudge?**

## Hypotheses

- **H1 (accuracy — the binding metric).** The best decision model classifies the
  labelled set **≥ 90%** correctly. A wrong "no action needed" skips the fix
  entirely; a wrong "action needed" costs one nudge round.
- **H2 (latency).** Its **median latency ≤ 200 ms**, so the check is cheap next to
  a turn.
- **H3 (beats the baseline).** It matches or beats a `qwen3.5:4b` chat call
  answering the same question (the router's current mechanism: a model call that
  parses JSON).

## Variables

- **Decision models:** `nimble`, `tev1`, `tev1:0.8b` via `/v1/systemone`, a
  `choice` question (`action` | `answer`).
- **Baseline:** `qwen3.5:4b` asked the same question and told to answer JSON.
- **Labelled set (fixed, committed with this plan):** ~20 requests —
  - `action` (~10): create/read/edit/rename/delete a file, run a task, search —
    **including stated goals** ("we want to create X"), which is exactly where the
    live turn failed.
  - `answer` (~10): questions about facts, memory, or explanation.
- **Measured per request:** the model's choice, its confidence, and wall-clock.

## What counts as evidence

- **Accuracy on the labelled set is the number.** It decides whether the judgment
  is reliable enough to gate a nudge.
- Latency decides whether it is cheap enough to run every turn.
- The baseline decides whether a decision model is actually *better* here, or just
  different.

## Falsification conditions

1. Best decision-model accuracy **< 80%** → not reliable enough; do not use it to
   gate the nudge (fall back to the router's own judgment).
2. Best accuracy **≥ 90%** AND median latency ≤ 200 ms → adopt it for this
   judgment.
3. If the **4B baseline** matches the decision models on accuracy at comparable
   latency → the decision models add nothing *here*; keep the router.

## Method

`experiment.py` calls `/v1/systemone` (decision models) and `/api/chat` (baseline)
against local Ollama. No brain, no tools, no network beyond localhost.

```bash
python assistant/experiments/decision_model_routing/experiment.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- **~20 requests is a probe**, not a rate; it can show a gap this large, not
  certify one.
- **"Needs an action" is a judgement**, and some requests are genuinely both (a
  question the assistant might answer from memory *or* by reading a file). Those
  are labelled by whether the request asks for an *operation*, and the ambiguity is
  named rather than hidden.
- **One threshold, one run.** A `choice` returns a decision and a confidence; a
  near-tie is a tie, and a second run could flip it.
- The decision model judges the **request**, not whether the turn *completed* it —
  that is the loop's structural check (no tool call), not this model's job.
