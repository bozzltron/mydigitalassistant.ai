# Experiment: is the one-nudge-per-turn bound costing writes?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The tool loop nudges the model **at most once per turn**, and the counter is shared:
`stream_tool_loop` sets `action_nudged = True` when it hands the model its own message
back, and `_maybe_nudge` returns `None` whenever `already_nudged` is set.

So if the **action** nudge fires (round 1 ended with no tool call) and the model then
calls a **read** — not the write — the **completion** nudge (`decision.outstanding`,
built for exactly "a tool ran but the action is outstanding") can never run. The two
nudges are mutually exclusive.

Is that suppression costing writes?

## Hypotheses

- **H1 (the gap, deterministic).** On a read-instead-of-write turn, the loop finalizes
  with no write and the completion judgement is **never consulted**. Proven with a
  scripted model — no real model, no brain.
- **H2 (it happens, live).** On real write-class requests, the suppressed case occurs:
  the action nudge fires and the turn ends with no successful `write_file`.
- **H3 (the fix would fire, live).** On those suppressed turns, `decision.outstanding`
  says *outstanding* — so a per-kind bound would have nudged.

## Falsification conditions

1. H1 false — the completion judgement **is** consulted on the shape → there is no gap;
   stop.
2. H2 gives **≤ 1 in 10** → the suppression costs too little to change; keep the single
   bound. A negative result.
3. H2 gives **≥ 3 in 10** AND H3 holds for those turns → implement the per-kind bound
   (one nudge of each kind, at most two rounds), and verify live.

## Variables

- **Arm A (deterministic, no model, no brain).** A scripted LLM through
  `stream_tool_loop` on a scratch store, in two shapes:
  - **S (the shape):** announce → *(action nudge)* → `read_file` → finalize.
  - **C (control):** `read_file` → *(completion nudge)* → finalize.
  Measures: whether a write happened, how many `acting` stages, and whether
  `decision.outstanding` was called.
- **Arm B (live).** Five write-class phrasings × 2 runs, through the **real pipeline**
  on the dev backend (the synthetic dev brain — never live). Per turn, parsed from the
  SSE stream: whether the action nudge fired (an `acting` stage before any `tool_call`),
  the tool calls in order, and whether a `write_file` ran. On turns that end without a
  write, the completion judgement is replayed offline.

## What counts as evidence

The **frequency of the suppressed case** (H2) is the number that decides. H1 decides
whether the case is even possible; H3 decides whether a fix would fire on it.

## Method

```bash
# Arm A needs no network; Arm B needs the dev backend + local Ollama.
python assistant/experiments/nudge_bound/experiment.py
```

Arm A writes only to a scratch store. Arm B sends requests to the dev backend over
`localhost:8443` and creates files named `nudge_bound_*` in the dev sandbox, which it
deletes again at the end.

`result.md` is written only after `verification.md`.

## Threats and limits

- **Ten live turns is a probe**, not a rate.
- **The chat model is non-deterministic.** The shape may be absent in one run and
  present in the next; ten turns bounds the frequency only coarsely.
- **A scripted model proves the loop's policy, not the world.** H1 is about the code.
- **Arm B runs against the dev brain**, which it mutates (files, episodes). The dev
  brain is synthetic and disposable; the live brain is never touched. This is a
  deviation from the "no brain" norm of the earlier nudge probes, named here rather
  than hidden.
- The completion judgement is replayed **offline** on the captured tool calls — it is
  not what the loop did, because the loop could not run it.
- **Not measured:** whether the second nudge makes the real model write. That is
  verified live after shipping, as with the first nudge.
