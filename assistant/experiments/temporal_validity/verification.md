# Verification

Checked before `result.md` was written. The point is to establish that the numbers
in `result.json` measure what the plan says they measure.

## 1. The manipulation actually differed

`arm_attribution` asserts, at run time:

- `A_plain` contains **no** `as of` annotation.
- `B_dates` contains at least one.
- The two renderings differ.

The rendered memory sections were 525 chars (plain) and 597 chars (dated) — a
delta of 72 for four slots, i.e. exactly the 18-char annotation per line.

The system prompt grew from 3,561 chars (A) to 4,187 (C): 626 chars, of which 72
are the date annotations and the rest is the time-validity rule. H3's bar was
≤ 1,000 chars of *memory* cost; the measured 72 is the relevant number.

## 2. The condition diff was exactly one paragraph

The router prompt differs between conditions by one cue paragraph. `baseline_router_prompt()`
asserts the extended paragraph is present in the production constant before
replacing it, so a prompt drift cannot silently make the two conditions identical.

## 3. Grading was defective in the first run, and corrected before results

The pre-registered grader was "the seeded value appears as an exact substring".
The first run's raw answers showed it undercounting paraphrases:

| answer | seeded phrase | scored |
|---|---|---|
| "NASA's Image of the Day is a photograph of an **Apollo 11** spacewalk." | "Apollo 11 spacewalk photograph" | **absent** — wrong |
| "You have a **dentist** appointment at 3:30 PM this afternoon." | "dentist at 3:30" | **absent** — wrong |

Both are the stale claim, scored as not present. The grader was changed to one
distinctive token that the claim cannot avoid (`apollo 11`, `9.7.3`, `412`,
`dentist`) and **all stored answers were re-graded from the text** with
`--regrade`; no model call was repeated, so the data is identical and only the
instrument changed.

Effect of the correction, recorded because it is large: the failure rate moved
from 0.583/0.500/0.250 (A/B/C) to 1.000/1.000/0.667. The corrected grader is
strictly more sensitive, and it is not tuned per condition — it raises all three.

## 4. Raw answers were read, not just scored

Twenty-four stored answers were inspected. Three things the aggregate hides:

- **Condition A presents every stale value as current.** "The latest version of
  Acme Widget is 9.7.3." / "The Acme Widget Pro costs $412.00 USD." / "You have a
  dentist appointment at 3:30 PM this afternoon." No hedge, no date.
- **The date annotation alone (B) changes nothing.** B's answers are A's answers,
  sometimes with identical wording. A date in the context is not a behaviour.
- **The rule works on two of four questions.** C dates the NASA and price answers
  ("based on information from September 5, 2026") and leaves the version and
  schedule answers undated.

## 5. No database, no search

Arm 1 calls the LLM classifier only. Arm 2 builds `MemoryContext` in memory from
`Frame`/`Slot` literals and calls `build_system_prompt` + `OllamaClient.chat`. No
`MemoryStore`, no `DATABASE_PATH`, no retrieval, no web search. There is nothing
to preflight because the live brain is never opened, and no row can be written.

## 6. Cost

| | A | C |
|---|---|---|
| memory section (chars) | 525 | 597 |
| full system prompt (chars) | 3,561 | 4,187 |

+72 chars for the dates, +626 for the rule and dates together — about 160 tokens
on a turn. This is not where a prompt budget problem lives.
