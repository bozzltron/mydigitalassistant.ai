# Experiment: does an explicit terminal declaration beat the `needs_action` judgement?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The tool loop treats *any message with no tool call* as the final answer, so
`"I'll now create X"` is indistinguishable from a completed turn. The `needs_action`
judgement (`tev1:0.8b`) exists only to disambiguate that: it decides whether the
*request* needed an action, so the loop can nudge when prose arrives instead. That
judgement costs a model, a threshold, a scope, a second judgement, and a shared bound.

The `finalize` tool already exists ("Signal completion. Ends the loop.") and the loop
already handles it — but nothing **requires** it. If ending a turn required an explicit
`finalize`, then "prose with no finalize" is incomplete **by construction**, and the
judgement disappears: the trigger becomes structural instead of judged.

Does that work — does the model comply, and does the structural check catch
announced-but-not-act at least as well, without extra cost?

## Hypotheses

- **H1 (compliance — the binding risk).** Under a contract that requires ending with
  `finalize`, the model calls `finalize` on **≥ 90%** of answer-class turns. Below that
  the structural check re-prompts most turns and is worse than the judgement.
- **H2 (completion).** On change-class turns the structural policy performs the action
  at **≥** the judged policy's rate. This is the real test: the structural check accepts
  a `finalize` that skipped the action, so a decline here means the judgement is
  load-bearing after all.
- **H3 (cost).** Mean model rounds per turn under the structural policy are
  **≤ judged + 0.5** — the terminal call rides in the round the answer would have used.
- **H4 (the mechanism, deterministic).** A scripted model that replies with prose and no
  tool call is re-prompted **structurally**, with the judgement never consulted, and then
  completes.

## Falsification conditions

1. H1 fails (< 90%) → the structural check re-prompts most turns → keep the judgement.
2. H1 holds but H2 fails (completion worse) → the structural check accepts skipped
   actions → keep the judgement.
3. H1, H2 and H3 hold → adopt the terminal declaration; retire `needs_action` and its
   model.

## Variables

- **Policy C (current):** the current contract; the nudge armed by
  `needs_action(request)`; trigger = no tool call.
- **Policy F (proposed):** a contract that **requires** ending with `finalize`; the nudge
  armed **structurally** (no `finalize` seen); trigger = no tool call and no `finalize`.
- **Requests (11, paired):** answer-class (5), change-class (4), announce-inducing (2) —
  the last being the phrasing the original live failure used.
- **Harness:** the real loop, the real chat model, the real tools, a scratch store and
  scratch sandbox. Arms are **interleaved per request** so both see the same state.
- **Measured per turn:** whether `finalize` was called, whether the action happened,
  model rounds, wall-clock, and whether the nudge fired.

## What counts as evidence

**Compliance (H1) is the number that decides**, because it is the cost of the
structural check. **Completion (H2) is the number that protects the judgement** — it
catches the hole the structural check cannot see. Rounds (H3) decide whether the win,
if any, is free.

## Method

`python assistant/experiments/terminal_declaration/experiment.py` — needs local Ollama.
Scratch store and sandbox only; no brain, no network beyond localhost.

`result.md` is written only after `verification.md`.

## Threats and limits

- **11 requests is a probe**, not a rate.
- **The system prompt is built without memory context** — the harness has no brain — so
  the contract is measured against a thinner prompt than production carries.
- **The chat model is non-deterministic.** Arms are interleaved and paired to control
  for it; one run per request is still coarse.
- **The structural check has a known hole:** it accepts a `finalize` that skipped the
  action. H2 measures whether that hole is hit, rather than assuming it is not.
- **Not measured:** whether the model obeys the terminal contract in production, where
  the prompt carries memory and the tools carry state.
- A scripted model (H4) proves the loop's policy, not the world.
