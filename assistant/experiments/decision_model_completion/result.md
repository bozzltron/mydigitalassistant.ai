# Result — can a decision model judge whether the action was done?

**Yes, but not the same one.** `nimble` judges it at **93.8%** with a **0.63 margin**
between outstanding and satisfied. The model we picked for `needs_action`
(`tev1:0.8b`) **cannot** — its two means are 0.438 vs 0.420, indistinguishable. So
the complete fix needs **two** decision models, which is a fleet cost, not a free
win.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — accuracy ≥ 90%** | **holds for `nimble`** (93.8%) | `tev1` 81.2%, `tev1:0.8b` 62.5% |
| **H2 — margin ≥ 0.3** | **holds for `nimble`** (0.626) | `tev1` 0.217, `tev1:0.8b` 0.017 |

## Falsification conditions, resolved

1. best accuracy < 80% → not triggered (93.8%).
2. best accuracy ≥ 90% **and** margin ≥ 0.3 → **triggered** (`nimble`).
3. `nimble` separates but the `needs_action` model does not → **triggered**.

## The finding

- **One judgement, two different models.** `tev1:0.8b` is the best `needs_action`
  judge (95% / 70 ms) and the worst at this one (62.5%, no margin). `nimble` is the
  reverse (90% / 604 ms for routing; 93.8% here). There is no single model that is
  good at both, so the fix is a **two-model** design unless we accept the slower
  model everywhere.
- **The failure it catches is exactly the live one.** The `outstanding` cases are
  "the request asked for a create/delete/rename and only a *read* ran" — the shape
  that left `Friend Music Records Release Template.md` uncreated while the assistant
  said it had created it.

## The complete design

1. **`needs_action`** (`tev1:0.8b`, 70 ms, every turn): does the request need an
   action? — built and committed (`486b277`).
2. **`outstanding`** (`nimble`, 604 ms, only when (1) is true): did the tool calls
   fail to perform it? If so, nudge once more with a note that names the gap.

`nimble` runs only on turns that already need an action, so its 604 ms is rare — but
it is a **9.5 GB model** in the fleet, on demand.

**Alternative, one model:** use `nimble` for both judgements. Simpler fleet, but it
adds ~530 ms to **every** turn (604 ms vs 70 ms on the hot path) and drops
`needs_action` from 95% to 90%. The two-model split is better for latency; the
one-model split is better for the fleet. That is a real trade, recorded here rather
than decided by taste.

## What this does not fix

- **Whether the nudge makes the model act.** The first nudge already showed the
  model will call *a* tool and still claim the write; the second nudge is judged the
  same way — measured on the live scenario, not assumed.
- **The underlying model behaviour** — a 9B that claims actions it did not take.
  These judgements are a guard around it, not a cure.
