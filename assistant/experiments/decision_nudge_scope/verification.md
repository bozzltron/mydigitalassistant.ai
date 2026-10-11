# Verification — decision_nudge_scope

## What was run

`experiment.py` on 2026-10-10 against local Ollama 0.35 (`/v1/systemone`). 28
labelled requests × 2 questions × 3 models. No brain, no tools.

```
docker run --rm -v $(pwd):/app -w /app -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/decision_nudge_scope/experiment.py
```

## Non-mutation

Pure model I/O. No brain is opened, so `preflight.py` does not apply.

## Raw output (summary)

Firing = P(positive) ≥ 0.4 (the deployed bias). Target = fire iff `class` is in the
question's `target_classes` (change for the first two; change **or** search for the
union).

```
question               model      change_recall  read+ans fired  search fired target acc   median
needs_action           tev1:0.8b          10/10            4/15           3/3      0.750      72ms
needs_action           tev1               10/10            6/15           3/3      0.679     304ms
needs_action           nimble             10/10            7/15           3/3      0.643     607ms
needs_change           tev1:0.8b          10/10            0/15           0/3      1.000      74ms
needs_change           tev1               10/10            0/15           0/3      1.000     347ms
needs_change           nimble             10/10            0/15           0/3      1.000     673ms
needs_change_or_lookup tev1:0.8b          10/10            0/15           3/3      1.000      80ms
needs_change_or_lookup tev1               10/10            7/15           3/3      0.750     351ms
needs_change_or_lookup nimble             10/10            0/15           3/3      1.000     677ms
```

The first two arms reproduced across two runs (4/15 and 0/15 unchanged), so the
near-tie concern did not bite here.

## The union arm (addendum)

`needs_change_or_lookup` on the deployed model (`tev1:0.8b`) is **1.000 target
accuracy**: change 10/10, search 3/3, read+answer 0/15, at 80 ms. It restores the
search guard change-only dropped, without re-admitting the read band. `nimble` also
scores 1.000; `tev1` does not (it fires on 7/15 read/answer). The deployed model is
the right one for this wording.

## The cases behind the numbers

Under the **deployed** config (`needs_action`, `tev1:0.8b`, threshold 0.4), the
nudge fires on **four** read-class requests that an answer can satisfy:

| Request | P(action) |
|---|---|
| Show me the contacts I've saved | 0.490 |
| Read Why Not Release.md | 0.441 |
| List my open tasks | 0.427 |
| What's in my ReadingList.txt? | 0.425 |

and stays silent on the others — including "How many books are on my reading list?"
(0.251) and "What did my morning briefing find yesterday?" (0.302). **No `answer`
case fires** under any model.

The change-scoped question fires on **none** of them, at P(change) 0.05–0.15, while
firing on all ten `change` cases at P(change) 0.79–0.99.

## The mechanism

The 0.4 threshold was added by the routing probe to **recover** one false-negative
read ("Read Why Not Release.md", P=0.441). That is exactly the band the other three
reads sit in (0.425, 0.427, 0.490). So **firing on reads is the intended behaviour of
the current design** — the threshold was tuned to make it happen. The scope question
is whether that intent is right, and the scoped question removes the band entirely
rather than re-tuning the threshold against it.

## Threats and limits (what remains after the run)

- **28 requests is a probe**, not a rate.
- **Read labels are arguable.** "Read Why Not Release.md" names a file; "List my open
  tasks" enumerates memory objects. A reader could call those operations. They are
  labelled `read` because an answer can satisfy them — which is the claim under test.
- **One run, low confidences.** The routing probe saw confidences of 0.01–0.35; a
  near-tie can flip.
- **Classification only.** This measures how each question classifies. Whether the
  model then performs the action on a read-class turn is a behavioural check on the
  live scenario, not here.
- **Search coverage was checked, and the router only partly owns it.** The router
  forces search only when `plan.sufficiency == MemorySufficiency.NONE`
  (`orchestrator.py:1450–1466`), so a lookup with a relevant-but-stale memory fact is
  **not** forced. This is why the recommended scope is the **union** (change + lookup),
  not change-only — see the addendum.
