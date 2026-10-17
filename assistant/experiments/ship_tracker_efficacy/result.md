# Result

K = 10, real LLM, real web search, brain copy, scratch sandbox. Pre-registration in
`plan.md` (amended once, before data, for the `type`/`vesselType` and CSV-shape findings).
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
