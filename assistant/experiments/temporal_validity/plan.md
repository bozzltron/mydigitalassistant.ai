# Experiment: does dating memory stop stale facts from being presented as current?

**Status:** pre-registered before data collection. This file is committed before the
harness is run; `result.md` is written only after `verification.md`.

## Question

"NASA image of the day" produced a two-day-old photo described as today's. The
stored fact was from a previous day, the prompt showed no date for it, and the
model had no way to know it had expired. Does fixing that change behaviour?

Two effects, measured separately:

1. **Routing.** Does a period-scoped question ("of the day", "today", "right now")
   trigger search more often when the router's freshness cues name those phrases?
2. **Attribution.** Given the same memory and no search, does showing each fact's
   observation date — plus a rule about period-scoped claims — stop the model from
   presenting a stale value as current?

## Why this experiment exists

The search side already dates what it returns: `_format_search_result` appends
`(2026-10-19)` when the backend supplies a publish date, and the extraction prompt
tells the model not to state a stale fact as current. The memory side has no
equivalent. `format_memory_context` renders confidence, source and reliability for
every slot and never renders `updated_at`, even though the value is stored and
loaded. The agent therefore cannot distinguish "the image of the day is X", learned
yesterday, from the same sentence learned this morning — while the router's
freshness list (`latest, current, recent, new, now, this year`) does not contain
"of the day" at all.

That is a claim about the prompt, and this experiment tests it.

## Hypotheses

- **H1 (routing).** Adding period-scoped cues ("today", "tonight", "this morning",
  "of the day", "daily", "right now", "as of") raises `wants_search` on
  period-scoped questions, without raising it on stable ones.
- **H2 (attribution).** Rendering each memory fact as `as of YYYY-MM-DD`, plus a
  rule that a period-scoped claim is valid only for its date, lowers the rate at
  which the stored value is repeated **without** its date.
- **H3 (cost).** Dating costs at most ~1,000 characters on a full memory section
  (~40 slots), and does not change answer length materially.

## Variables

### Arm 1 — routing

**Independent:** the router system prompt, two versions — the current cue list
(`latest, current, recent, new, now, this year`) and the extended one.

**Dependent:** `wants_search` (bool), `search_query` (for inspection).

**Query set:** 10 period-scoped questions and 10 stable ones (definitions, personal
preferences, stored facts). The stable set is the **false-positive control**: a cue
list that makes everything search is not an improvement.

### Arm 2 — attribution

**Independent:** three presentation conditions, same memory, same questions:

| Condition | Memory rendering | System prompt |
|---|---|---|
| A (baseline) | `key = value (conf, src, reliability)` | current |
| B | A + `as of YYYY-MM-DD` per slot | current |
| C | B | B + the time-validity rule |

**Dependent:** `cites_date`, `repeats_stale_value`, `presents_stale_as_current`,
answer chars.

**Query set:** 4 questions whose answers are period-scoped and whose only source
is the seeded memory. Two are about a real subject (so the model has pretraining
knowledge to prefer or defer to), two about fictional subjects (so memory is the
only possible source and attribution is unavoidable).

**Seeded memory** (built in-memory, never written to the live brain):

| frame | slot | value | observed |
|---|---|---|---|
| `nasa_apod` | `image_of_the_day` | "Apollo 11 spacewalk photograph" | 45 days ago |
| `acme_widget` | `latest_version` | "9.7.3" | 45 days ago |
| `acme_widget` | `price_usd` | "412.00" | 45 days ago |
| `today_schedule` | `afternoon` | "dentist at 3:30" | 45 days ago |

## Grading (deterministic, no judge model)

- `repeats_stale_value` — the seeded value appears in the answer (exact substring).
- `cites_date` — the answer contains the observation date in any of the shapes the
  prompt could produce: ISO `2026-09-05`, `Sep 5`, `September 5`, or a year.
- `presents_stale_as_current` — **the failure**: `repeats_stale_value` **and not**
  `cites_date`.

Grading is blind to condition and cannot be inflated by a longer answer: the
failure requires the stale value to be present, and a date anywhere in the same
answer clears it. Raw answers are stored so the regex can be reviewed against
them rather than trusted.

## Controls

- Same chat model, `temperature = 0.7`, `think = False`, same system prompt
  builder, same (empty) history, same user.
- **Search is off in Arm 2.** The question is attribution, not whether search
  finds the right page; a live result would introduce internet variance into the
  comparison. This is a stated scope limit, not an oversight.
- The memory context is constructed identically for every condition, so retrieval
  is constant by construction.
- Conditions are interleaved and order-rotated within each replicate.
- 3 replicates per question per condition: the chat model at 0.7 is not
  deterministic.
- Router calls use `temperature = 0.0` (the production setting for routing) and
  one replicate; if any query flips, the effect is smaller than the noise and that
  is reported.

## Stated scope limits

- Arm 2 measures **presentation**, not retrieval. It does not ask whether the old
  fact would have been retrieved at all.
- Arm 1 measures `wants_search`, not whether search then found today's page.
- The time-validity rule is evaluated as prompt text; it is not a validity-window
  schema. If H2 fails, the schema is a candidate follow-up, not a conclusion.
- The extended router prompt is a literal snapshot in the harness. If the
  production prompt changes later, Arm 1 must be re-run with the new baseline.

## Falsification (decided before the run)

This experiment reports a **null result** if any of these hold:

1. **H1 ceiling.** Baseline `wants_search` on period-scoped queries is already
   ≥ 0.8 — there is no room for the cue list to help.
2. **H1 over-trigger.** The extended cue list raises `wants_search` on the stable
   query set by more than 0.10. A cue list that searches for everything has not
   improved routing.
3. **H2 no effect.** `presents_stale_as_current` does not fall by at least 0.50
   absolute between A and C, or `cites_date` does not rise by at least 0.50.
4. **H2 ceiling.** Baseline already cites a date in ≥ 0.80 of answers, meaning the
   model was already attributing from the prompt's current date and the date per
   fact adds nothing.

A null on H2 is reported as a null. The next step in that case is the validity
window (`valid_as_of` on the slot), not a second attempt with different wording.

## Success criteria

- [ ] Every condition renders the same facts; only the date annotation and the
      rule differ (manipulation validity, asserted not assumed)
- [ ] Arm 1: `wants_search` on period queries rises A→B by ≥ 0.20 absolute
- [ ] Arm 1: `wants_search` on stable queries rises by ≤ 0.10
- [ ] Arm 2: `presents_stale_as_current` falls A→C by ≥ 0.50
- [ ] Arm 2: `cites_date` rises A→C by ≥ 0.50
- [ ] H3: dated rendering costs ≤ 1,000 chars on the seeded context
- [ ] No database is written; the live brain is never opened

## Threats

- **Temperature noise.** 3 replicates of 4 questions is 12 observations per
  condition; a small effect could hide in it. The bar is deliberately 0.50 because
  the expected effect (a date either appears or it does not) is coarse.
- **The regex can credit an unrelated date.** An answer that says "today is
  October 20 and the version is 9.7.3" would count as citing a date while still
  presenting the stale value. This inflates `cites_date` and therefore *understates*
  the failure rate, so the primary metric errs against the hypothesis. Raw answers
  are kept to check this by eye.
- **Pretraining leakage.** The NASA question has a real answer the model may
  prefer, which would clear the failure without any dating. That is why the
  fictional questions are in the set: they cannot be answered from training.
- **Prompt-side effects.** The time rule could make the model hedge on things it
  should answer directly. Not measured here; it is visible in the stored answers.

## Run

```bash
docker run --rm -i --network mydigitalassistantai_appnet \
  --add-host host.docker.internal:host-gateway \
  -v "$(pwd):/app" -w /app \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  mydigitalassistantai-assistant python -m assistant.experiments.temporal_validity
```

No brain copy is mounted: Arm 2 builds its memory context in memory and Arm 1
makes no database call at all, so there is nothing to preflight. The live DB is
never opened.
