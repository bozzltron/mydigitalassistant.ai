# Experiment: ship-tracker task efficacy and consistency

**Status: pre-registered.** This file was written before any trial was run. `result.md`
comes after `verification.md`, never before.

## The question

Given a real, user-authored task prompt, how **effective** and how **consistent** is the
agent? Effectiveness is "did it do the thing"; consistency is "does it do the thing the
same way twice". They are different questions and a task can pass one and fail the other.

The prompt under test, verbatim:

```
1. Look up ships we are tracking in the vessels.csv
2. Pull vessel location data from available sources (e.g., AIS feeds, maritime databases)
3. update the ship-tracker.geojson file. For each vessel, include:
     shipName: Proper name of the vessel
     type: Vessel type (e.g., Container Ship, Bulk Carrier)
     location: Current position or region (e.g., 'Near Suez Canal', 'Indian Ocean')
     description: A human-readable string combining ship name, type, and location for
                  map display (e.g., 'EVER GIVEN - Container Ship near Suez Canal')
4. Read the ship-tracker.geojson file to make sure our changes above were written and
   are accurate.
5. If the file is not updated, try again.
```

## Why this experiment exists

Three things about this task are known from one live run, and none of them is a
measurement:

1. **It failed once, and the reason was mechanical.** `ship_tracker` read `vessels.csv`,
   searched the web, and finalised without ever calling `write_file`. The completion nudge
   that exists for exactly that turn was unreachable (fixed in `6f3d888`). One run cannot
   tell us whether the fix made the task reliable.
2. **The model's tool choice varies.** A later attempt called
   `search_file(vessels.csv, query='MAERSK ESSEX IMO 9632630')` — a substring search with a
   compound string — and got 114 characters back, where the earlier run used `read_file`
   and got the whole file. Same task, different affordance, different information.
3. **The window is not the constraint.** Peak prompt was 13485/16384 (82%) with
   `truncated=False` on every one of 13 observed turns. Whatever limits this task, it is
   not the context window — so the experiment must not be designed around that hypothesis.

The point of measuring is that "the agent can do this" and "the agent does this" are
different claims, and only the second one is useful to a user who set a daily task.

## Design

**Single arm, repeated measures.** The same prompt, K = 10 trials, identical seed state.
There is no treatment to vary: the question is the distribution of outcomes, not the
effect of an intervention. A one-arm design is the honest one — inventing a second arm to
compare against would be measuring something nobody asked about.

**Why 10.** The outcome is binary, so 10 trials bound the failure rate we can see
(0 failures in 10 ⇒ ≲ 26% at 95%, by the rule of three) and that is enough to separate
"works" from "flaky". It is **not** enough to resolve a 10% difference, and the result must
say so rather than implying precision it does not have.

**Seeding.** A scratch sandbox (`EXP_SANDBOX`) is seeded per trial with:
- `vessels.csv` — the five vessels, as the user's own file has them.
- `ship-tracker.geojson` — a **known-wrong** state (an empty FeatureCollection), so
  "the file was updated" is detectable from content rather than from a timestamp.

**Isolation.** `preflight.py` gates the run: the DB must be a copy, the live volume must
not be mounted, and the copy's SHA-256 is recorded. Table digests after the run prove the
live brain was not touched. **The scratch sandbox is asserted, not assumed** — the runner
aborts unless `EXP_SANDBOX` is set and is outside the live sandbox, the way
`file_write_parity` does it.

## Amendment, made before any trial

Two things about the real inputs were checked while wiring the runner, and one changes the
scoring — so it is recorded here rather than discovered in the result.

1. **`vessels.csv` is a bare list of names** — no header, no types. The seed therefore uses
   the file's own content verbatim. A synthetic `ship_name,ship_type` file would hand the
   model the `type` field it is supposed to have to look up, which would flatter `fields_complete`.
2. **The prompt asks for `type`; the user's own `ship-tracker.geojson` uses `vesselType`.**
   Either key now satisfies the field. Scoring `vesselType` as a failure would measure the
   *prompt's* inconsistency with the user's data rather than the agent's competence — and
   the agent following the schema already in the file is the better behaviour, not the
   worse one. This is a scoring decision, so it had to be made before the data, not after
   seeing which key the model chose.

## Amendment 2 — the instrument, before run 4

Runs 1–3 could not answer the question they raised. This document declares `nudges` as
dependent variable 7, and the runner reported `nudges: 0` for every trial in every run —
because `one_trial` initialised `nudges: list[str] = []` and **never appended to it**. Run 3's
claim that "the nudge is firing" was therefore an *inference from the call lists*, not a
measurement; the record was silent either way. That is the gap that let runs 2 and 3 be
explained by guesswork twice, and it was a bug in the instrument rather than a missing idea.

Three fields are added. All are reads on the run; none is a decision about it.

| field | what it now records | why |
|---|---|---|
| `answer` | the final answer text, as stored on the task frame | whether the model *claims* it wrote the file or says it could not — the difference between a claim/reality gap and a silent decline |
| `call_log` | each call with its identifying argument (`path=…`, `query=…`) | `read_file, read_file` cannot distinguish reading `vessels.csv` from reading `ship-tracker.geojson` back, and step 4 instructs exactly that read |
| `nudges` | the kinds that actually fired (`empty` / `completion`) | the declared variable, now collected instead of reported as zero |

`_maybe_nudge` is **wrapped, not modified** — it already returns the note it hands the model,
so the observer records and delegates, and cannot change a trial's behaviour.

**No phrasing detector is added to the instrument.** It is tempting to score "the answer
claims a write" as a boolean, and that would be a regex over wording — the thing this project
forbids as a *trigger*. A boolean here would be a judgement wearing a count's clothes. The
text is captured, and read.

Consequence for interpretation: runs 1–3 lack these fields, so **run 4 is a new observation,
not a repetition.** H1 and H5 keep their meaning as rate hypotheses over K=10; the three-way
distinction the instrument now draws (claim / silent decline / write failure) is descriptive,
and one run of ten resolves it no further than the classes it prints.

## Dependent variables, per trial

| # | Variable | Definition |
|---|---|---|
| 1 | `written` | `ship-tracker.geojson` differs from the seeded empty FeatureCollection |
| 2 | `valid` | it parses, and its `type` is `FeatureCollection` |
| 3 | `coverage` | vessels in the file ÷ vessels in `vessels.csv` |
| 4 | `fields_complete` | every feature has non-empty `shipName`, `type`, `location`, `description` |
| 5 | `description_shape` | `description` matches `"<shipName> - <type> <location>"` |
| 6 | `tool_calls` | the ordered call list — *how* it got there |
| 7 | `nudges` | how many action nudges fired |
| 8 | `outcome_class` | see below |

**`outcome_class` is the most important variable, and it exists because the naive scoring
would be wrong.** The task conflates two different asks: *fetch current positions* and
*write them*. An agent that finds the data is five days stale and says so instead of
writing it as current has done the *right* thing — and a binary `written` would score it a
failure. So each trial is classified:

| class | meaning |
|---|---|
| `wrote_complete` | written, all four fields, every vessel — the intended outcome |
| `wrote_partial` | written, but missing vessels or fields — **the worst outcome, because it looks done** |
| `refused_stale` | did not write, and said why (data could not be made current) — arguably correct |
| `no_attempt` | did not write, and never tried — the `6f3d888` failure |
| `error` | the trial crashed or the model errored |

A bare "written" rate would merge `refused_stale` with `no_attempt`, which is exactly the
distinction that tells us whether to fix the agent or the prompt.

## Hypotheses and falsification

Each is falsifiable, and the bar is stated **before** the data.

| # | Hypothesis | Falsified if |
|---|---|---|
| H1 | The task completes: `wrote_complete` in ≥ 8 of 10 | ≤ 7 of 10 |
| H2 | It is consistent: the modal `outcome_class` covers ≥ 8 of 10 | no class reaches 8 |
| H3 | No vessel is silently dropped: `coverage = 1.0` whenever `written` | any trial writes a partial file |
| H4 | The schema contract holds: `fields_complete` and `description_shape` whenever `written` | any trial writes a malformed field |
| H5 | The `6f3d888` fix holds: `no_attempt` = 0 | any `no_attempt` |

**H1 is the headline.** If it fails, the answer to "is the agent effective at this task"
is *no*, and the `outcome_class` distribution says which of the four failures to work on.
**H5 is the regression check** for the nudge fix — it is the only hypothesis that is about
code rather than the model, and it should be clean.

## The bar, in advance

> If fewer than 8 of 10 trials produce a complete file, this task is not yet reliably
> achievable, and I will say so plainly rather than reporting the parts that worked.
>
> If `wrote_partial` occurs at all, that is worse than a refusal: a file that looks
> complete and is not. I will treat a single instance as a defect worth fixing ahead of
> any rate improvement.

## Threats, named before the data

1. **Web search is not deterministic.** The available position data varies by run, so a
   failure may be *data* rather than *agent*. This is why `outcome_class` separates
   `refused_stale` from `no_attempt`: the first is the world, the second is the agent.
   It also means H1 could fail for a reason no code change fixes, and the result must say
   which happened rather than blaming the model.
2. **The prompt asks for two things at once** (fetch, and write). A trial that fetches well
   and declines to write stale data is a *prompt* finding, not an agent finding. I expect
   at least one, given the one live run did exactly this.
3. **10 trials is small.** 8/10 and 10/10 are not distinguishable at this N. No p-values
   will be reported; the counts are the result.
4. **Model residency.** Ollama holding chat + utility + embeddings affects latency, not
   the binary outcomes. Latency is reported as context, never as a hypothesis.
5. **The agent's own memory is seeded from the live brain copy**, so it may recall prior
   ship-tracking episodes. That makes the trials *not* independent of the user's history —
   which is realistic, but means the result describes *this* install, not a cold start.
   Recorded, not corrected: a fresh brain would be a different experiment.
6. **A trial can pass by accident.** `wrote_complete` does not prove the positions are
   *correct* — only that the shape is right and every vessel is present. Correctness of a
   fetched coordinate is not measurable here without ground truth, and claiming it would be
   the "false negative produced by a no-op manipulation" this project has been burned by.

## Cost

K = 10 full cognitive loops with web search: roughly 10 × (20–50 s) ≈ 4–8 minutes of wall
clock, plus model load. Each trial writes to the scratch sandbox and the scratch DB only.

## What this experiment does not answer

**Whether fewer tools per turn would help.** That is a separate question with a separate
design (see the sibling plan, `toolset_scope/`), because it needs an intervention and a
control, and because the observed failure — `search_file` where `read_file` was meant — is
a *semantic* mismatch between two affordances, not a symptom of there being twenty-one of
them. Measuring efficacy first is what tells us whether that second experiment is worth
running at all: if `wrote_complete` is 10/10, tool-set trimming is a cost optimisation, not
an efficacy fix.

## Amendment 3 — run 5's fix and its prediction, before data

**The fix.** At the ceiling, if the request's action is still unperformed, the loop spends the
completion nudge and grants `_CEILING_GRACE_ROUNDS = 2` rounds instead of falling through to a
wrap-up that sets `tool_choice="none"` and asks the model to "state what blocked you". Bounded:
spending the nudge sets `completion_nudged`, so the grant can happen at most once.

**A new instrument field: `exit_reason`.** The loop now logs `tool_loop_exit: reason=…`
(`answered` / `finalize` / `exhausted`) at INFO, and the runner records it. Run 4 had to
establish that trials 1 and 7 exited through the ceiling *by construction* — inferring it from
the absence of a nudge in the record — because the wrap-up's answer is not distinguishable from
an ordinary one. It is now observed.

**Judged on mechanism, not rate.** K=10 cannot resolve 7→9, so the primary claim is about the
exit path.

| trial | run 4 | predicted run 5 |
|---|---|---|
| 1, 7 | `no_attempt`, exit `exhausted` | `wrote_complete`, exit `answered` — the ceiling grants the round the nudge needs |
| 2–5, 8–10 | `wrote_complete`, exit `answered` | unchanged; they never reached the ceiling |
| 6 | `no_attempt` after **both** nudges | **still `no_attempt`** — the completion nudge did fire within budget and the model still claimed it had no write access. A different mechanism, which this fix does not address |

Bars, set before the data:

- **H1 ≥ 8/10** — the standing bar, unchanged.
- **`exit_reason == "exhausted"` count must be 0.** That is falsifiable at K=10, which a
  two-trial rate difference is not.

Falsification conditions:

1. A trial exiting `exhausted` with its action unperformed means the grant did not do its job.
2. A trial exiting `answered` with no write *and* no nudge in its record means the grant fired
   and the model refused anyway — which moves the problem from the loop's structure to the
   model's belief, and is then the next thing to measure.
3. Trial 6 writing is not evidence for the fix — it was never on the exhaustion path. It would
   mean its run-4 failure was noise, and should be treated as such rather than as a win.
