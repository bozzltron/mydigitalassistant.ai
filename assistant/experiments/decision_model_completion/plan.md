# Experiment: can a decision model judge whether the action was done?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The `needs_action` nudge (`assistant/experiments/decision_model_routing/`) made a
live turn call *a* tool — but it called `read_file` and then **claimed the write**,
so the file still was not created. "No tool call" is too coarse a signal.

The next half is a **completion judgement**: given the request and the tool calls
that actually ran, is there an action the user asked for that the calls did **not**
perform? Judging from the **tool calls**, not the assistant's answer — a probe showed
the answer's own claim ("I've created…") fools the judgement (P(completed) ≈ 0.93).

**Can a local decision model make that judgement reliably?**

## Hypotheses

- **H1 (accuracy — the binding metric).** The best decision model separates
  `outstanding` from `satisfied` at **≥ 90%** on the labelled set.
- **H2 (a usable margin).** Its mean P(outstanding) on `outstanding` cases exceeds
  its mean on `satisfied` cases by **≥ 0.3**, so a threshold exists that is not a
  coin flip.

## Variables

- **Decision models:** `nimble`, `tev1`, `tev1:0.8b` via `/v1/systemone`, a `noul`
  question about `{request, tools_called}`.
- **Labelled set (fixed, committed with this plan):** ~16 (request, tool-calls)
  pairs —
  - `outstanding` (~8): the request asked for an action and the calls did not do
    it — **including the live case** (create a template; only `read_file` ran).
  - `satisfied` (~8): the action was performed, or none was needed.
- **Measured:** P(outstanding) per case, the decision at 0.5, and wall-clock.

## What counts as evidence

- **Separation is the number.** This judgement gates a second nudge, so it must not
  fire on satisfied turns (a spurious nudge costs a round) and must not miss
  outstanding ones (which is the whole point).
- A single model that both separates well *and* is already in use for
  `needs_action` is preferred to two models in the fleet.

## Falsification conditions

1. Best accuracy **< 80%** → no decision model can make this judgement; stop, and
   treat the model's claim-accuracy as a model-capability limit rather than a loop
   bug.
2. Best accuracy **≥ 90%** AND margin ≥ 0.3 → build the second nudge.
3. `nimble` separates but the `needs_action` model (`tev1:0.8b`) does not → the fix
   needs **two** decision models, which is a fleet cost to weigh, not a free win.

## Method

`experiment.py` calls `/v1/systemone` against local Ollama. No brain, no tools.

```bash
python assistant/experiments/decision_model_completion/experiment.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- **~16 cases is a probe**, not a rate.
- **The `tools_called` string is a summary** the loop must produce; its format is
  part of the design and could itself influence the judgement.
- **One threshold, one run.** Confidences on this API are low (the routing probe
  saw 0.01–0.35), so a near-tie can flip.
- This judges whether the *calls* satisfy the request; it does not verify the
  file's contents, only that the right kind of call happened.
