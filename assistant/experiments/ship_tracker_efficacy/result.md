# Result

> **VOID — runs 1–3 measured a broken harness, not the agent.** `init_store()` was called
> without `search_tool`, so every `web_search` in all 30 trials returned
> `Search tool not initialized`. The model read the CSV, read the map, searched, was told the
> search had failed, and honestly reported that it could not get positions — which scores as
> `no_attempt`. Nothing below about completion, nudges, or the checks is a measurement of the
> agent. See `verification.md`, "Runs 2 and 3 — INVALID, retrospectively". Run 4 follows
> below once it completes.

K = 10, real LLM, real web search, brain copy, scratch sandbox. Pre-registration in
`plan.md` (amended twice, before data: the `type`/`vesselType` and CSV-shape findings, then
the instrument — answer text, tool arguments, real nudge counts, tool errors).
The invalid first run is in `verification.md`.

## Outcome classes

| class | n | |
|---|---|---|
| `wrote_complete` | **3**/10 | written, four fields, all five vessels |
| `no_attempt` | **7**/10 | did not write, never called a write tool |
| `wrote_partial` | 0/10 | — |
| `refused_stale` | 0/10 | — |
| `error` | 0/10 | — |

## Hypotheses

| # | | verdict |
|---|---|---|
| H1 | complete in ≥ 8/10 | **FAIL** — 3/10 |
| H2 | modal class ≥ 8/10 | **PASS** — `no_attempt` covers 7, one short |
| H3 | no silent partial write | **PASS** — 0 `wrote_partial` |
| H4 | schema contract holds when written | **PASS** — 3/3 `fields_complete`, `coverage = 1.0`, description shaped |
| H5 | `no_attempt` = 0 | **FAIL** — 7 |

**The agent is not reliably effective at this task.** It is effective *sometimes* — 3/10 is
not zero, and those three trials are complete and correct in shape — but a user who sets
this as a daily task gets a stale file seven mornings in ten.

## The finding that matters is in the call lists

Read them as two groups:

```
no_attempt     read_file | search_file | read_file,search_file | read_file ×4
wrote_complete …write_file…  (all three reach a write)
```

**Every trial that failed read the CSV and stopped. Every trial that succeeded reached
`write_file`.** The failure is not that the model cannot do the task — trial 10 did it in
three calls (`read_file`, `read_file`, `write_file`) — it is that it *stops after reading*
and reports.

That is H5 failing, and H5 is the regression check for `6f3d888`. **So the nudge fix was
necessary and is not sufficient.** The wiring now lets the completion judgement run — and
the completion judgement is a *model* judgement (`decision.outstanding`, `nimble`) that a
`read_file` of the CSV satisfies. `decision.py` already records the measurement:

> ``nimble`` is useless at ``outstanding`` (no separation)

This run is that measurement reproducing live, 7 times out of 10, on the task the judgement
was written for.

## Why this is not a prompt problem

`plan.md` predicted `refused_stale` — the model declining to write stale positions, as the
one live run did. **It did not happen once.** The seven failures are not the model
exercising judgement about data quality; they are the model answering as though the task
were "report the vessels". The prompt's step 3 says *update the ship-tracker.geojson file*
and step 5 says *if the file is not updated, try again*, and the model read that, read the
CSV, and wrote prose.

So the prompt is not the lever here, and neither is the context window (measured at 82%
peak, `truncated=False` on every observed turn — the plan ruled it out in advance).

## What to do next, in order

1. **Make the completion trigger structural, not a judgement.** The project's own principle
   for the *first* nudge is "the trigger is structural (no tool call)". The second has no
   such anchor: it asks a small model whether the calls did the job, and it says yes to a
   read. The structural fact available is that **the request needs a *change* and no
   mutating tool ran** — `write_file`, `edit_file`, `append_file`, `delete_file`,
   `rename_file` — and `decision.py` already scopes `needs_action` to exactly that class
   ("a *change* (create/edit/rename/delete/append/move) or an *external lookup*"). So the
   outstanding check can be: *a change was requested, and no mutating tool was called.*
   Deterministic, cheap, and it does not ask a 0.8B model to tell a read from a write.
2. **Re-run this experiment** after that change. H5 is the falsifiable target: `no_attempt`
   should go to 0, and H1 should follow. If it does not, the next hypothesis is that the
   model needs to be told *which* tool performs a write — which is the `toolset_scope`
   question, and it becomes the efficacy fix rather than the cost one.

## Run 2 — after the structural completion check (`cca58f3`)

Same instrument, same seed, K=10, brain copy taken after run 1.

| class | run 1 | run 2 |
|---|---|---|
| `wrote_complete` | 3 | **4** |
| `no_attempt` | 7 | **5** |
| `refused_stale` | 0 | **1** |
| `wrote_partial` | 0 | 0 |
| H1 / H5 | FAIL / FAIL | **FAIL / FAIL** |

```
trial  3,4,6,10: no_attempt   read_file, fetch_url
trial  9:        no_attempt   fetch_url,fetch_url,fetch_url
trial  7:        refused_stale  read_file,fetch_url,write_file,read_file
```

**The fix was falsified, and by its own mechanism.** Every remaining `no_attempt` called
`fetch_url` — which is in `_LOOKUP_TOOLS` — so `action_performed` returned true and no nudge
fired. The rule I wrote was:

```python
return bool(called & _MUTATING_TOOLS) or bool(called & _LOOKUP_TOOLS)
```

**The `or` is the bug.** A request to *change* something is not performed by a *lookup*, and
the rule accepts either. The seven-to-five improvement is real but incidental: run 1's
failures called only reads, run 2's called reads *and* a fetch, and only the fetch reached
the permissive branch.

### What run 2 establishes beyond the falsification

- **`refused_stale` appeared**, exactly as `plan.md` predicted and run 1 did not produce. Trial
  7 called `write_file` and left the file an empty FeatureCollection — declining to write
  positions it could not confirm. That is the *correct* behaviour, and it is why the class
  exists: scored as `written`, it would have been a fifth failure.
- **The model reaches for the network when it cannot find data.** Trials 3, 4, 6, 9, 10 all
  fetched. So the "read the CSV and stop" pattern of run 1 was partly a *no-search* pattern,
  and the fetch is progress — it just is not the write.

### The corrected rule

The kind of the request has to decide which tool set counts, which means `needs_action` must
return the kind rather than a bool. It already asks the model; it is throwing the answer's
*shape* away.

| request kind | performed when |
|---|---|
| change | `called & _MUTATING_TOOLS` |
| lookup | `called & _LOOKUP_TOOLS` |

No extra model call — the judgement is already made — but the question gains a third option
(`change` / `lookup` / `answer`), which **invalidates the `needs_action` measurement** in
`experiments/decision_nudge_scope/`. That measurement has to be redone, and the plan for it
should be re-read before this change is made.

## Run 3 — after the kind-aware rule (`cca58f3` + the `kind` change)

| class | run 1 | run 2 | run 3 |
|---|---|---|---|
| `wrote_complete` | 3 | 4 | **1** |
| `no_attempt` | 7 | 5 | **8** |
| `refused_stale` | 0 | 1 | 1 |
| H1 / H5 | FAIL / FAIL | FAIL / FAIL | **FAIL / FAIL** |

```
trial  2: read_file,search_file
trial  3: read_file,search_file ×5
trial  4: read_file,web_search ×6,read_file,web_search
trial  8,9,10: read_file,read_file
```

**The rule now works and the outcome got worse.** A `fetch_url` no longer satisfies a
change, so the completion nudge fires on the read-and-stop trials — and the model answers
the nudge with *more looking*. Trial 4 called nine tools, six of them searches, and never
wrote. Trials 3 and 8–10 are reads alone.

Read the direction honestly: 8 vs 5 `no_attempt` and 1 vs 4 complete is consistent across
two classes, but **K=10 cannot resolve this** and the plan said so in advance. What it is
is *suggestive*: the nudge consumes a round and produces more of the behaviour it was
meant to interrupt.

### What that means for the hypothesis

Three runs have now varied the *check*:

| run | check | result |
|---|---|---|
| 1 | model judgement (`outstanding`) | 3 complete |
| 2 | structural, either tool set | 4 complete |
| 3 | structural, kind-aware | 1 complete |

The check is no longer the problem — run 3's nudge fires on exactly the trials it was
written for. **The model is being told, correctly and specifically, that it did not write
the file, and it responds by reading again.** So "the model does not realise" is falsified
as the mechanism, and the nudge is not the lever.

### The next measurement, not the next fix

The runner still cannot distinguish three states, and they need different fixes:

1. the model *believes* it wrote the file (a claim/reality gap);
2. the model *knows* it did not and declines silently — the `refused_stale` behaviour
   without the report;
3. the model is trying and failing at the write itself.

`refused_stale` occurred twice across three runs, which is evidence for (2): the model does
exercise this judgement, and a judgement it does not voice looks identical to a failure.
**Capture the final answer text per trial** and read it. That is a change to the
instrument, not to the agent, and it should have been done before run 3 — I flagged the gap
and then theorised anyway.

## What this does not establish

- **Not correctness.** `coverage` and `fields_complete` say the shape is right; the
  coordinates were never checked against ground truth, and the plan says so.
- **Not a cold-start rate.** The trials ran against a copy of this install's brain, so the
  agent may recall prior ship-tracking episodes. A fresh brain is a different experiment.
- **Not the cost numbers.** `peak_prompt_tokens` and `tool_results_dropped` were not wired
  into the runner, so this run says nothing about the window. That was a gap in the
  instrument, not a finding — and the cost question belongs to `toolset_scope` anyway.
- **Not 3/10 ± anything.** Ten trials cannot resolve a 10% difference. Three successes and
  seven failures is a direction and a call-list pattern, not a rate with error bars.

## Run 4 — the first valid measurement: 7/10

| class | run 1 | run 2 | run 3 | **run 4** |
|---|---|---|---|---|
| `wrote_complete` | 3 | 4 | 1 | **7** |
| `no_attempt` | 7 | 5 | 8 | **3** |
| `refused_stale` | 0 | 1 | 1 | **0** |
| H1 / H5 | F / F | F / F | F / F | **F / F** |

With the search tool actually wired (`verification.md`), the agent does the task most of the
time. H1 (>= 8/10) still fails. The bar was set before the data and the run did not meet it.

```
trial  2..5, 8..10: wrote_complete  coverage=1.00 fields=True  nudge=empty,completion
trial  1:           no_attempt      nudge=empty                 6 calls, all reads
trial  6:           no_attempt      nudge=empty,completion      7 calls
trial  7:           no_attempt      nudge=empty                 5 calls
trials nudged 10/10 . completion nudges 8 . tool errors 1
```

### The nudge is load-bearing, and I had it backwards

Every writing trial shows one sequence: the model answers with no tool call, gets the **`empty`**
nudge, reads something, gets the **`completion`** nudge (a read cannot perform a change), and
then calls `write_file`. All seven writes came after a completion nudge.

The run-3 conclusion that "the model answers the nudge with more looking" was an artifact of the
unwired search tool: it was re-searching because searching kept failing.

### What the model actually says — no earlier run could show this

The failures are not confusion. They are **invented limitations and requests for permission**:

| trial | verbatim |
|---|---|
| 6 | "I couldn't update the ship-tracker.geojson file because **I don't have direct write access**" |
| 7 | "**I cannot directly write** new GeoJSON features ... without having the complete vessel data" |
| 1 | "I need you to confirm: **should I write** the complete geojson file ... now?" |
| 2 (wrote) | "I cannot pull fresh real-time AIS positions from external maritime databases in this enviro..." |

All false: `write_file` exists and succeeded in seven trials. Trial 2 makes the same claim and
writes anyway, because the completion nudge overrides it.

### The defect: exhaustion is answered by a report, with tools disabled

Trials 1 and 7 recorded only the `empty` nudge, yet each made tool calls. **That is a proof by
construction**: a round ending with no tool call *must* pass through `_maybe_nudge`, so the only
path to a final answer that skips it is the round ceiling — `while turn < max_turns`
(`max_tool_rounds = 6`) falling through.

That path does two things, both measured here:

1. It **skips `_maybe_nudge` entirely**, so the completion nudge — the thing that produced all
   seven writes — is unreachable on the one path where "did not act" is most likely.
2. It makes a wrap-up call with **`tool_choice="none"`** and a prompt asking the model to "state
   what you were able to do, **what blocked you** ... and what you need from them next". Acting
   is impossible by construction, and the prompt *invites a blocker*. Trials 1 and 7 complied
   with "I don't have direct write access" and "should I write ... now?".

The model is not failing to act. At the moment its rounds run out, it is asked to explain why it
did not act — by a prompt that can only produce a reason.

All seven successes used exactly 4 calls; the two unexplained failures used 5 and 6. The ceiling
is the variable.

### What this does not establish

Whether the completion nudge is *necessary* — no counterfactual arm ran, and none of the seven
writes happened unprompted — and whether raising the ceiling or spending the nudge on the
exhaustion path is the better fix. Both are testable; H1 remains the bar.
