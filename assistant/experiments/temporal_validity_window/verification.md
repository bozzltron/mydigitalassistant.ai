# Verification

Checked before `result.md`. The question is whether the three renderings differ in
exactly the way the plan says, and whether the numbers measure attribution rather
than something else.

## 1. Manipulation validity

`render_conditions()` asserts at run time:

- `as_of` contains `as of` and **no** `valid for`.
- `window` contains `valid for` and **no** `days old`.
- `window_age` contains `(45 days old)`.

The window conditions set `valid_as_of` on every slot; `as_of` passes `None`, so
the *same six facts* are present in all three conditions and only the annotation
differs. The memory sections were 882 chars (`as_of`) and 990 (`window_age`): +108
for six slots, of which +18 per slot is the wording and the rest the ages.

## 2. Fixed clock

`TODAY = "2026-10-20"` is passed to `format_memory_context(today=...)` and to the
prompt's `current_datetime`. Nothing reads the wall clock, so `45 days old` is the
same number whenever this is re-run.

## 3. Every answer repeated the stale value

`repeats_stale_value` is **1.000 in all three conditions**. No answer declined to
use the fact; no condition produced an empty answer. The conditions therefore
differ only in *whether a date accompanied the claim*, which is exactly what
`cites_date` measures and what the failure definition keys on. This also means the
experiment cannot tell us anything about whether the model would abstain — it
never did.

## 4. Grading, and one generous rule

Grading uses the distinctive-token rule from the first experiment (`apollo 11`,
`9.7.3`, `412`, `dentist`, `memphis`, `37`), not exact substring; the previous
`verification.md` §3 records why.

One pattern was **added** for this run: `\d+ days? old` counts as citing a date.
Condition C expresses the date as an age, so scoring it as undated would penalise
the treatment for using its own representation. The rule is generous in the same
direction as the pre-existing year/month patterns: any date token anywhere in the
answer clears the failure.

That generosity masks a real case. Condition `window_age` on "How many did we sell
today?" produced:

> Based on the available information, you sold **37 units** today. (Note: This data
> is from September 5, 2026; …)

The first sentence asserts today's number; the parenthetical retracts it. It is
scored as attributed because a date is present. Counted as written, the failure
rate is therefore a **floor**, not a ceiling — the true rate of "asserts stale as
current" is at least as high.

## 5. Raw answers read

All 54 stored answers were inspected at least once. The three residual failures in
the best condition share a shape: the answer takes its tense from the **question**
("today", "this afternoon", "right now") and ignores the fact's window, even with
`(45 days old)` printed beside it.

## 6. No database

`MemoryContext` is built from `Frame`/`Slot` literals. No `MemoryStore`, no
`DATABASE_PATH`, no retrieval, no search. `Slot.valid_as_of` is a model field that
nothing in the store writes yet, so the live brain is untouched by construction.
