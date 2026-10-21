# Result

> **The harness for this run was removed with the feature it measured.** It
> required `Slot.valid_as_of` and `split_expired`, both deleted when the live
> check showed the stale fact was carried by a prior episode rather than a slot.
> The plan, the data in `result.json`, and the grading are unchanged; the harness
> is in git history at the commit that closed the line.

54 generations (6 questions × 3 conditions × 3 replicates), `qwen3.5:9b`, temp 0.7,
time-validity rule on, search off, fixed clock `2026-10-20`.

## Headline

| condition | rendering | stale-as-current | cites a date | repeats value |
|---|---|---|---|---|
| `as_of` | `as of 2026-09-05`, all in place | **1.000** | 0.000 | 1.000 |
| `annotated` | `valid for 2026-09-05 (45 days old)`, all in place | **0.556** | 0.444 | 1.000 |
| `split` | same, under `## Expired — may no longer apply` | **0.222** | 0.778 | 0.889 |

## Verdict

- **H1 supported.** Placement beats annotation by 0.334 (0.556 → 0.222), well over
  the 0.20 bar. Moving a fact out of the usable section does what writing a
  warning next to it could not.
- **H2 met.** 0.222 ≤ the 0.25 bar set two experiments ago for building the write
  path.
- **H3 violated as written, in the right direction.** `repeats_stale_value` fell to
  0.889, and the two answers responsible are the best in the run — the model
  declining an expired fact outright:

  > "I do not have information on the current price of the Acme Widget Pro, as my
  > memory only contains an outdated price from September 5, 2026."

  The plan treated any drop as collateral. That premise was wrong: abstention is
  the outcome this work is for. The primary metric is unaffected by those rows,
  so the 0.222 stands.

The progression across the three experiments, on the same six questions and the
same grader:

| representation | stale-as-current |
|---|---|
| plain memory, no date | 1.000 (first run, 4-question set) |
| `as of` + rule | 0.889–1.000 |
| `valid for` + age | 0.444 |
| expired section | **0.222** |

## What still fails

Four of eighteen, and they are the same three shapes: the answer takes its tense
from the question.

| question | answer |
|---|---|
| my schedule (2 of 3) | "Your dentist appointment is at 3:30 PM this afternoon." |
| my package (1 of 3) | "Your package is currently in Memphis, Tennessee." |
| units sold (1 of 3) | "37 units were sold today." |

So the section is not a guarantee. It takes the failure rate from "always wrong"
to "wrong once in five", and the residue is a model behaviour that more prompt
structure has not fixed in three attempts.

## Decision

**Build the write path.** The representation now clears the bar that was set in
advance for it.

Scope, in order:
1. Extraction marks a slot `volatile` when the claim is period-scoped — a model
   decision in the extraction schema, not a keyword list.
2. `upsert_slot` persists `valid_as_of = today` for a volatile slot (one column,
   one migration; `Slot.valid_as_of` already exists).
3. The orchestrator passes `split_expired=True`. **Until (1) and (2) land this is
   inert** — no slot carries a window — so it can be turned on now without
   changing a single live answer. That is deliberate: the switch and the data are
   separate, and the switch is the part that was measured.
4. Re-run this experiment's six questions against a *real* brain afterwards, with
   the same grader. The synthetic context is the reason to still be careful: one
   fact per frame, no live facts alongside. The 0.25 bar applies to the real
   brain too, and the first live run is the check, not a victory lap.

## Why this did not ship

Before the write path was built, the live assistant was asked the NASA question
three times against the real brain (prod, rule and dates live):

| rep | searched | answer |
|---|---|---|
| 1 | yes | correct — today's APOD |
| 2 | **no** | the same answer, from memory |
| 3 | **no** | the same answer, again |

The memory context made the source visible:

```
[2026-10-20 · assistant · 78% match] Today's NASA Image of the Day (October 20, 2026) is …
```

So the stale carrier was a **prior assistant episode**, not a slot. The reason the
agent did not refresh is upstream of the answer: the reasoner judged memory
sufficient, and its prompt never sees the time-validity rule, which is added to
the chat prompt later. A window on `Slot` would not have touched the case that
broke.

That is a carrier mismatch, not a missing representation: the 0.222 above stands
for slot-carried facts (prices, versions, schedules), but nothing writes slots
for this one. The field, the expiry renderer, and the expired section were
removed; the observation dates and the time rule were kept (both live and free).
The next lever, if this line is reopened, is the **reasoner's sufficiency
decision** — not another schema.

## What is not yet known

- **Frame-level expiration.** A frame with one live and one expired slot stays in
  the usable section. Production frames mix slots; the real-brain run will show
  whether that matters.
- **Who decides "volatile".** The utility model. If it over-marks, stable facts
  start being filed as expired; if it under-marks, the feature is quietly inert.
  That is the next measurement, and it needs its own instrument (a labelled set of
  claims) rather than this harness.
- **The 3 remaining failure shapes** need retrieval-time suppression or a model
  with better temporal reasoning, not a fourth section.
