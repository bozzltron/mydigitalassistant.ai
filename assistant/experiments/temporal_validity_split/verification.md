# Verification

Checked before `result.md`.

## 1. Manipulation validity

`render_conditions()` asserts at run time:

- The **slot lines are identical** between `annotated` and `split` — placement
  changed, nothing else. Without this the comparison would be confounded by the
  annotation itself.
- `## Expired — may no longer apply` is absent from `annotated` and present in
  `split`.
- Every frame appears under `### ` exactly once (three `acme_widget` frames, so
  the check is per-name count, not per-name uniqueness).
- The expired heading precedes every frame heading — nothing leaked into the
  usable section.
- `as of` and `valid for` are present where expected.

Memory-section cost: 882 chars (`as_of`), 990 (`annotated`), 1,024 (`split`). The
section adds 34 characters over the inline annotation — the heading plus a
newline.

## 2. All six facts are expired in every condition

The window is `2026-09-05`, "today" is `2026-10-20`, so every seeded slot is 45
days past its window. In the `split` condition that means `## Relevant memory` is
empty and all six frames are under the expired heading. This is the strongest form
of the test — there is no live fact to anchor the usable section — and it is a
stated scope limit: a production context would normally have both.

## 3. Passing the threshold does not mean the model always abstains

Raw answers read, all 18 `split` rows. Four still assert the stale value as
current:

| count | question | answer |
|---|---|---|
| 2 | my schedule | "Your dentist appointment is at 3:30 PM." / "… at 3:30 PM this afternoon." |
| 1 | my package | "Your package is currently in Memphis, Tennessee." |
| 1 | units sold today | "Based on the available information, 37 units were sold today." |

These are the same three shapes that survived the previous experiment. The
section raises the rate of explicit retraction from 0.444 to 0.778; it does not
make the model stop answering.

## 4. H3's premise was wrong, and the data says so

The plan treated any drop in `repeats_stale_value` as collateral ("the section
reads as ignore this"). It fell 1.000 → 0.889, and the two answers responsible are
the *best* in the run:

> "I do not have information on the current price of the Acme Widget Pro, as my
> memory only contains an outdated price from September 5, 2026."

> "Based on the retrieved memory state, I do not have information about today's
> sales (the data available is dated 2026-09-05)."

The model declining an expired fact is the desired outcome, not a cost. H3 is
reported as violated-as-written and reinterpreted, with this evidence, in
`result.md`. The metric that matters (`presents_stale_as_current`) is unaffected by
those two rows: they have no repeated value.

## 5. Grading is unchanged

Same distinctive-token markers and the same date patterns as
`temporal_validity_window`, including `N days old`. No grading rule was added or
changed after seeing this run, so the 0.222 is directly comparable to the
previous 0.444.

## 6. No database

Same construction as the previous two runs: `Frame`/`Slot` literals, no
`MemoryStore`, no retrieval, no search. `Slot.valid_as_of` still has no writer and
no column, so nothing in the live brain can be affected.
