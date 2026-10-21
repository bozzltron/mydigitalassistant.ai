# Experiment: does moving expired facts into their own section stop stale answers?

**Status:** pre-registered before data collection. `result.md` is written only
after `verification.md`.

## Question

`temporal_validity_window` raised the attribution rate from 0.111 to 0.556 with
`valid for DATE (45 days old)`, but 0.444 of answers still asserted a 45-day-old
value as current. The failures took their tense from the question — "today",
"this afternoon", "right now" — overriding the annotation printed on the fact.

This run tests whether **placement** does what **annotation** could not: render
facts past their window under their own heading instead of inside
`## Relevant memory`.

## Why this is the cheap test worth running

The fact is currently *present in the usable set*, and the model treats presence
as currency. Moving it to a section that says it may not apply changes the
structure the model reads, not the words next to the value — and it costs one
heading. If it fails, the representation line of work is finished and the next
step is retrieval, not more prompt.

## Hypotheses

- **H1 (placement).** C lowers `presents_stale_as_current` versus B by ≥ 0.20.
- **H2 (sufficiency).** C reaches ≤ 0.25 absolute, the bar the previous run set
  for building the write path.
- **H3 (no collateral).** C does not reduce `repeats_stale_value` — the facts are
  still offered, just under a warning, so the model should still be able to cite
  them with a date. A drop there would mean the section reads as "ignore this".

## Variables

**Independent:** the memory rendering. The same six seeded facts in every
condition, all stamped `valid for 2026-09-05` where a window is set; "today" is
pinned to `2026-10-20`.

| Condition | Window | Placement |
|---|---|---|
| A (`as_of`) | none, falls back to `as of` | all under `## Relevant memory` |
| B (`annotated`) | `valid for … (45 days old)` | all under `## Relevant memory` |
| C (`split`) | `valid for … (45 days old)` | expired frames under `## Expired — may no longer apply`, after the usable section |

**Dependent:** `repeats_stale_value`, `cites_date`, `presents_stale_as_current` —
identical definitions to the two preceding experiments, so the numbers are
comparable.

## Questions

The same six as `temporal_validity_window`, unchanged, because the 0.25 bar was
set on this set. Re-using them is deliberate: this is the third run against the
same instrument, and changing questions would break the comparison.

| # | question | marker |
|---|---|---|
| 1 | What's NASA's image of the day? | `apollo 11` |
| 2 | What's the latest version of Acme Widget? | `9.7.3` |
| 3 | What does the Acme Widget Pro cost right now? | `412` |
| 4 | What's on my schedule this afternoon? | `dentist` |
| 5 | Where is my package right now? | `memphis` |
| 6 | How many did we sell today? | `37` |

## Implementation rule under test

A frame is **expired** when every slot that carries `valid_as_of` is older than
today. Frames with any live or undated slot stay in the usable section, and their
expired slots keep the inline annotation. Splitting a single frame's slots across
two sections would break the frame grouping the prompt depends on.

This is frame-level because the seeded data is one fact per frame. A production
version with mixed frames would need slot-level grouping; that is a stated limit,
not a hidden one.

The expired section is emitted **after** `## Relevant memory`, so the usable facts
come first. The heading is the only difference between B and C; the slot lines are
byte-identical (asserted in the harness).

## Controls

- Same chat model (`qwen3.5:9b`), `temperature = 0.7`, `think = False`, rule on in
  all conditions (it is production), search off, fixed clock `2026-10-20`.
- Same memory context; only the rendering differs.
- 3 replicates, condition order rotated per replicate.
- A and B are re-run in this session rather than compared against the previous
  run's numbers, because the two would otherwise be separated by a different
  model load.

## Falsification (decided before the run)

- **H1 null** if C is within 0.10 of B. Placement then does not matter and the
  representation line of work ends.
- **H2 null** if C ≥ 0.50, the previous run's "representation is not the lever"
  line.
- The result is reported as **partial** if C beats B by ≥ 0.10 but sits above 0.25;
  that outcome does not justify the write path either, and says so.

## Success criteria

- [ ] Manipulation validity: B and C differ only by the section heading and
      position; the slot lines are identical (asserted)
- [ ] Every expired frame appears in the expired section and no live frame does
      (asserted on the rendered string)
- [ ] C beats B by ≥ 0.20 (H1) or the null is reported
- [ ] C ≤ 0.25 (H2) or the result is reported as partial/null
- [ ] `repeats_stale_value` does not fall below 1.0 (H3 — the facts must remain
      usable)
- [ ] No database is opened

## Scope limits

- Frame-level expiration only, as above.
- Synthetic memory: one slot per frame, no competing facts.
- The heading's wording is not varied. If placement helps but 0.25 is still
  missed, the next variable is the wording of the heading or retrieval-time
  suppression, not a fourth annotation.

## Run

> **Historical.** The harness was removed with the `valid_as_of` / `split_expired`
> code it measured; this command no longer runs. The plan, data and grading are
> unchanged — see `result.md` for why the line was closed.

```bash
docker run --rm -i -v "$(pwd):/app" -w /app \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  mydigitalassistantai-assistant python -m assistant.experiments.temporal_validity_split
```
