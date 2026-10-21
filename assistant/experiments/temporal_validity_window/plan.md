# Experiment: does an explicit validity window beat an observation date?

**Status:** pre-registered before data collection. `result.md` is written only
after `verification.md`.

## Question

`temporal_validity` measured three prompt variants against a stale memory: plain,
`as of DATE`, and `as of DATE` + a time-validity rule. The rule fixed the reported
case but the failure rate only fell 1.000 → 0.667, short of the pre-registered
0.500 bar. The two questions it still failed were about **the user's own stored
records** ("my schedule this afternoon", "the latest version").

The hypothesis for this run: the model needs the claim to carry a **validity
window** — the period the claim covers — not merely the date it was observed.
Does that beat the current representation?

## Why this representation

`as of 2026-09-05` answers "when did we learn this". `valid for 2026-09-05`
answers "when was this true". For "my schedule this afternoon" the second is the
question being asked. A window also lets the renderer do the arithmetic the model
currently skips: `valid for 2026-09-05 (45 days old)`.

Whether the model needs the arithmetic done for it, or just the different wording,
is the second question — and it decides whether the window is a representation
change or a prompt change.

## Hypotheses

- **H1 (wording).** `valid for DATE` lowers `presents_stale_as_current` versus
  `as of DATE`, with the same rule in place.
- **H2 (arithmetic).** `valid for DATE (N days old)` lowers it further than the
  window alone. If it does not, the model can do the subtraction and the age is
  presentation cost without benefit.
- **H3 (cost).** The window adds ≤ 30 characters per slot over the current
  annotation, and the system prompt grows by ≤ 100 characters.

## Variables

**Independent** — memory rendering, rule held constant:

| Condition | Slot rendered as | Baseline comparison |
|---|---|---|
| A (`as_of`) | `… , as of 2026-09-05` | current production |
| B (`window`) | `… , valid for 2026-09-05` | A with the window |
| C (`window_age`) | `… , valid for 2026-09-05 (45 days old)` | B plus arithmetic |

**Dependent:** `repeats_stale_value`, `cites_date`, `presents_stale_as_current`
(same definitions as `temporal_validity`, so the numbers are comparable).

## Questions

The four from `temporal_validity` (so A is comparable) plus two of the same shape
that did not exist in the first run, to reduce the chance of fitting the wording
to four observed answers:

| # | question | seeded claim | marker |
|---|---|---|---|
| 1 | What's NASA's image of the day? | `nasa_apod.image_of_the_day` | `apollo 11` |
| 2 | What's the latest version of Acme Widget? | `acme_widget.latest_version` | `9.7.3` |
| 3 | What does the Acme Widget Pro cost right now? | `acme_widget.price_usd` | `412` |
| 4 | What's on my schedule this afternoon? | `today_schedule.afternoon` | `dentist` |
| 5 | Where is my package right now? | `package_tracking.location` | `memphis` |
| 6 | How many did we sell today? | `acme_widget.units_sold_today` | `37` |

All six facts are stamped `2026-09-05`; "today" is pinned to `2026-10-20`
(45 days). Every slot carries `valid_as_of`, so the window is what differs —
not which facts have one.

Grading is the distinctive-token rule from the first experiment (`verification.md`
§3 there explains why exact substring was wrong), plus one pattern added for this
run: `\d+ days? old` counts as citing a date, because condition C expresses the
date that way and scoring it as undated would penalise the treatment for using
its own representation.

## Controls

- Same chat model (`qwen3.5:9b`), `temperature = 0.7`, `think = False`, same
  `build_system_prompt` with the time-validity rule **on in all three conditions**.
  The rule is production now; this experiment asks what to put above it.
- Same memory context in every condition; only the slot annotation differs.
- Search off: this is an attribution question.
- 3 replicates, condition order rotated per replicate.
- The fixed clock is passed in (`2026-10-20`), never read from the wall clock, so
  `45 days old` is reproducible.

## Falsification (decided before the run)

- **Null on H1** if B's failure rate is within 0.10 of A's (0.667 ± 0.10), i.e. the
  window wording does nothing.
- **Null on H2** if C is within 0.10 of B.
- **Overall null** if the best condition's failure rate is ≥ 0.50. In that case the
  representation is not the lever and the next step is not more schema; it is a
  larger-n run to establish whether 0.667 is the model's floor.
- **Success** if the best condition's failure rate is ≤ 0.25 and beats A by ≥ 0.40.

The 0.667 baseline is from 12 observations, so a 0.10 band is about one answer.
The decision is deliberately coarse: this is a yes/no on building a write path.

## Scope limits

- **The write path is not built.** `Slot.valid_as_of` exists and renders, but
  nothing sets it in production yet: extraction would need to mark a slot
  period-scoped (a model decision, `volatile: bool`) and the write path stamp it.
  That is deliberately deferred until this experiment says the representation
  earns it. The field is inert in the live brain.
- Persistence (a `slots` column, migration) is deferred with it. The harness sets
  the field on the object.
- Same synthetic memory shape as the first run: one slot per frame, no competing
  facts. Real retrieval may behave differently.

## Success criteria

- [ ] Manipulation validity: A/B/C renderings differ only in the annotation, and
      assertions verify `as of` vs `valid for` vs `valid for … (N days old)`
- [ ] B beats A by ≥ 0.20, or the H1 null is reported
- [ ] C beats B by ≥ 0.20, or the H2 null is reported
- [ ] Best condition ≤ 0.25 failure, or the overall null is reported
- [ ] Cost within H3
- [ ] No database is opened

## Run

> **Historical.** The harness was removed with the `valid_as_of` field it measured;
> this command no longer runs. The plan, data and grading are unchanged — see
> `result.md` for why the line was closed.

```bash
docker run --rm -i -v "$(pwd):/app" -w /app \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  mydigitalassistantai-assistant python -m assistant.experiments.temporal_validity_window
```
