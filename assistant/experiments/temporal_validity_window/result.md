# Result

> **The harness for this run was removed with the feature it measured.** It
> required `Slot.valid_as_of`, which was deleted when the live check showed the
> stale fact was carried by a prior episode rather than a slot (see
> `../temporal_validity_split/result.md`, "Why this did not ship"). The plan,
> the data in `result.json`, and the grading are unchanged; the harness is in git
> history at the commit that closed the line.

54 generations (6 questions × 3 conditions × 3 replicates), `qwen3.5:9b`,
temp 0.7, time-validity rule on in every condition, search off, fixed clock.

## Headline

| condition | slot annotation | stale-as-current | cites a date |
|---|---|---|---|
| `as_of` (current) | `… as of 2026-09-05` | **0.889** | 0.111 |
| `window` | `… valid for 2026-09-05` | **0.611** | 0.389 |
| `window_age` | `… valid for 2026-09-05 (45 days old)` | **0.444** | 0.556 |

Every answer repeated the stale value (1.000 in all conditions). Nothing declined.
The conditions differ only in whether a date accompanied the claim.

## Verdict against the pre-registered bars

- **H1 (wording) supported.** The window beats the observation date by 0.278
  (0.889 → 0.611), over the 0.20 bar. Saying *when the claim was true* is
  measurably better than saying *when we learned it*.
- **H2 (arithmetic) not supported at the bar.** Adding `(45 days old)` moved
  0.611 → 0.444, a 0.167 improvement against a 0.20 bar. Direction right, size
  short. The model can do the subtraction on its own, at least sometimes.
- **Overall: partial, neither success nor null.** The best condition is 0.444 —
  above the 0.25 "build it" line, below the 0.50 "representation is not the lever"
  line. Read plainly: **44% of answers still assert a 45-day-old value as current**
  with the strongest annotation this experiment tested.

## What the residual failures look like

All three, in the best condition:

| question | answer | problem |
|---|---|---|
| NASA image of the day | "NASA's image of the day is a photograph from the Apollo 11 spacewalk." | present tense, no date |
| my schedule this afternoon | "You have a dentist appointment at 3:30 PM." | present tense, no date |
| where is my package | "Your package is currently in Memphis, Tennessee." | "currently", no date |

They share one cause: **the answer takes its tense from the question.**
"Today", "this afternoon", "right now" appear in the question and come back in the
answer, overriding a window printed on the fact. The three that *were* fixed did
it by retracting explicitly:

> "Based on my memory, the last recorded price for the Acme Widget was **$412.00**,
> but that information is from September 5, 2026, and may not reflect the current
> price today."

So the model can attribute when it notices; the window does not make it notice.

## Decision

**Do not build the write path.** The representation improved a lot and still
leaves a 44% wrong-answer rate on exactly the class of fact this was meant to
protect. Shipping a `volatile` extraction flag, a DB column, and a migration to
reach that number is not justified.

What stays in the tree:

- `Slot.valid_as_of` — a model field, **inert**: nothing writes it, no column. Kept
  because it is the representation the next experiment needs and because removing
  and re-adding it would lose the reasoning. If the next run fails too, delete it.
- `_validity_note` / `format_memory_context(today=…)` — renders the window when it
  is set, falls back to `as of` otherwise. With no slot carrying the field, live
  behaviour is unchanged from `temporal_validity`.
- `timeutil.local_date_str()` — the date the age is computed against, in the user's
  zone (the container runs UTC).

What does **not** ship: the `volatile` extraction flag, slot persistence, any
retrieval filter.

## Next experiment (not run)

The failures are structural, not lexical: the fact is *present* in the usable
memory section, and the model treats presence as currency. Two cheap variations,
both model-first, both testable with this harness:

1. **Separate the expired facts.** Render windows that are older than today under
   their own heading — `## Expired — may no longer apply` — instead of annotating
   them inside `## Relevant memory`. This tests whether *placement* beats
   *annotation*, without asking the model to notice anything.
2. **Ask the router for the horizon, not a keyword list.** The router already
   returns `wants_search`; have it also say whether the question is about a
   *period* (today / now / this week). Where it is, facts with expired windows are
   held out of the prompt entirely and the turn is told to search. That uses the
   model's own judgement once, instead of a hardcoded phrase list, and it is the
   only option here that can make the model *decline* rather than annotate.

(1) is hours; (2) is a day and changes the prompt contract. The bar stays 0.25
failure for the same six questions, and a run that cannot reach it should end this
line of work rather than add a third mechanism.
