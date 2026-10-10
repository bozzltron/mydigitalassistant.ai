# Result — does the auto-escalation fire for the right tasks?

**No — it is inverted.** The auto-escalation fires on **100% of mechanical file
tasks** and **0% of hard reasoning**. The clause meant to identify hard work
(`_is_multi_step`, via "and then") selects exactly the work that should stay on
the warm model.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — over-fires on mechanical tasks** | **holds, maximally** | tool_task **10/10 (100%)** |
| **H2 — still fires for hard reasoning** | **fails** | hard_reasoning **0/8 (0%)** |

## Falsification conditions, resolved

1. tool_task < 20% → not met (100%).
2. tool_task ≥ 50% → **triggered** (100%).
3. hard_reasoning < 50% → **triggered** (0%).

## What this cost, live

The turn that prompted this — *"I've updated muso-soup-offers.txt. Let's read it
and then regenerate Muso Soup Priority.md"* — escalated to `qwen3.8:27b` with
thinking on and a 12-round ceiling, and ran **15+ minutes** across 6+ tool rounds.
Every "and then" in a file recipe buys minutes of avoidable wait on the 27B.

## The fix

Auto-escalation keys on an **explicit reasoning cue**, not on a sequence phrase:

- `escalate = _has_explicit_think_intent(query)` — drop the
  `sufficiency in (PARTIAL, NONE) and _is_multi_step(query)` clause. This governs
  both thinking mode and the max tier, so a mechanical multi-step task no longer
  turns thinking on either.
- `_should_use_max_intelligence` = `escalate and sufficiency == NONE` — drop
  `_is_multi_step`.
- `_is_multi_step`, `_SEQUENCE_MARKERS`, `_CLAUSE_CONJUNCTIONS` become unused and
  are deleted (clean ship).
- **Visibility added:** the escalation decision and its reason are logged, because
  this investigation could not tell from the logs *why* a turn chose the 27B.

The manual "Max" toggle is unchanged — a user who wants the big model still has
it, and the UI already exposes it.

**Re-measured (same query set, after the fix):**

| Class | Before | After |
|---|---|---|
| `tool_task` escalation | 100% (10/10) | **0% (0/10)** |
| `hard_reasoning` escalation | 0% (0/8) | **50% (4/8)** |

The four that escalate are the ones carrying an explicit cue ("work through",
"step by step", "reason through"); the other four run on the chat model unless the
user toggles Max — the deliberate trade described below.

## What this does not fix

- **The MAX model's speed.** With the trigger fixed, mechanical tasks stay on the
  warm 9B, but a *genuinely* escalated turn still runs on the 27B. Whether the
  opus Q4 9B should be the max model is a separate, unmeasured question
  (`docs/MODEL_SELECTION.md`).
- **Hard queries with no explicit cue** now run on the chat model unless the user
  toggles Max. That is the deliberate trade: the multi-step proxy was doing more
  harm than good, and a model-judged difficulty signal (the router already runs
  one LLM pass) is a candidate follow-up rather than a second scripted heuristic.
