# Result — does an explicit terminal declaration beat the `needs_action` judgement?

**Not on the pre-registered bar — but it is correct, and the bar may have measured the
wrong axis.** Requiring the turn to end with `finalize` makes announced-but-not-act
incomplete by construction (proven deterministically), the model **complies** (4/4
answer turns declared completion), and completion is **unchanged** (change 4/4,
announce 2/2). It costs **~1–2 extra rounds on action turns** (median 2 → 4), which
fails H3. So per falsification condition 3, the judgement stays — but the reason is
cost, not correctness.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — compliance ≥ 90%** | **holds** | 4/4 answer turns called `finalize` under F (0/5 under C) |
| **H2 — completion ≥ judged** | **holds** | change 4/4 vs 4/4; announce 2/2 vs 2/2 |
| **H3 — rounds ≤ judged + 0.5** | **fails** | 2.70 vs 1.82 (median 2.5 vs 2); action turns 4 vs 2 |
| **H4 — structural catch, no judgement** | **holds** | deterministic: 2 rounds, finalized, judgement consulted 0 times |

## Falsification conditions, resolved

1. H1 fails → not triggered (100%).
2. H1 holds but H2 fails → not triggered (completion equal).
3. H1, H2 **and** H3 hold → **not met**: H3 fails → **keep the judgement**.

## The finding

- **The architectural idea is sound.** The ambiguity the judgement exists to resolve —
  "no tool call" could be an answer or an unfinished action — genuinely disappears when
  a turn must declare its end. The model complies without complaint (it is told to), and
  it does not skip actions to reach the declaration.
- **But it is not free.** The cost is not the `finalize` call; it is that naming an
  explicit end state makes the model **verify before declaring** — `write_file,
  read_file, finalize`, and once `write_file ×4`. On answer turns this is nothing
  (median 1 round); on action turns it is ~1–2 rounds.
- **The judgement and the declaration are not substitutes on the same axis.** The
  judgement costs ~70 ms *and a model, a scope, a threshold, and a shared bound*; the
  declaration costs ~1–2 rounds *and nothing else*. My pre-registered bar priced the
  first in the second's currency and so could only ever have returned "keep the
  judgement". That is a flaw in the bar, named here rather than buried.

## What this proves, and what it does not

- **Proved:** a structural terminal declaration catches the announce-and-stop shape
  with no judgement, and the model complies at 100% on answer turns and 100% on
  completion. The alternative is *valid*.
- **Not proved:** that it is *worth* it. The simplification (retire `needs_action`, its
  model, its scope, its threshold) is real and unmeasured by this probe; the latency
  cost is real and measured. Which matters more is a product call the probe cannot make.
- **Not proved:** whether a **tighter contract** removes the verification pass —
  "call `finalize` as soon as the work is done; do not re-check" — which is the obvious
  next lever if the round cost is the objection.
- **Not proved:** that the structural check's hole (a `finalize` that skipped the
  action) is never hit; it was not hit here, at n = 4–5.

## Recommendation

**Keep the judgement**, as pre-registered. But record the trade honestly:

- The terminal declaration is a **correct, simpler alternative** at ~1–2 rounds per
  action turn. If action turns are rare (measure the logs), the round cost is small and
  the fleet simplification is worth re-taking.
- The cheapest way to decide is a **contract tightening** — does "do not re-check before
  finalizing" remove the extra rounds? — which is a small variant of this same probe and
  the only thing standing between the two designs.
- The `require_terminal` path in `stream_tool_loop` and `build_system_prompt` is
  **retained** (additive, default-off, tested) so this probe is reproducible. If the
  idea is retired outright, remove them; they exist for this experiment and this
  documented alternative.
