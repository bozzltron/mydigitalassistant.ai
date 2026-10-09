# Result — does the router search on borderline queries?

**No. Borderline recall is 30% (3/10) — the falsification condition fired.** The
router under-searches exactly the class where a current source would help most:
recommendations and "recent research". It does not over-search stable knowledge
(specificity 100%).

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — borderline recall** | **FAILS** | **3/10 (30%)**, below the 80% bar and the 50% falsification floor. |
| **H2 — general-knowledge specificity** | **holds** | **8/8 (100%)**; no stable-knowledge query searched. |

## Falsification conditions, resolved

1. borderline recall < 50% → **triggered** (30%).
2. borderline recall ≥ 80% → not met.
3. general-knowledge specificity < 80% → not triggered (100%).

## The finding

The router keys on a **freshness vocabulary**, not on whether a current source
would improve the answer:

- searches: "latest …", "current state of …", "compare the top …";
- silent: "best X for a beginner", "which Y should I buy", "a good Z",
  "are X worth it", "is X still recommended", "recent research on …".

So "Explain recent research on lithium-ion battery degradation" is answered from
training while "Summarize the latest research on sleep and memory" searches — the
only difference is the word *latest*. The recommendation class never searches.

## Verdict

**The gap is real and systematic, not noise.** Combined with `search_trigger`:

- unambiguous external queries → the trigger is sound (8/8);
- stable knowledge → correctly quiet (8/8);
- **borderline / recommendation → under-searched (3/10).**

The fix is a **router bias toward search** for the recommendation and
"recent research" patterns, without disturbing the two classes that already behave.
It must be re-measured against both experiments after the change — a bias that
raises borderline recall by also searching stable knowledge would be a regression.

## Fix and re-measurement (post-registration)

The diagnosis above was pre-registered; the fix and its measurement are a
follow-up, recorded here so the number is not lost.

**Two changes:**

1. **Router prompt** (`task_router.py`): search is now warranted when a
   current/external source answers *better* — listing live facts,
   recommendations, "recent research", and freshness cues — not only when the
   answer is impossible without the web. A first draft that enumerated only
   categories and dropped the general "external info needs search" rule
   regressed "who won the most recent Austin mayoral election?" to no-search; the
   final wording keeps the general rule *and* the categories.
2. **Router force** (`orchestrator._run_turn`): the router's positive judgment now
   overrides the reasoner's coarse length heuristic (`_is_non_info_seeking`),
   symmetric with the existing veto — an explicit `wants_search` wins, but only
   when memory is empty (`sufficiency == NONE`), so a stored answer is not
   overridden.

**Re-measured (same pre-registered sets):**

| Experiment | Before | After |
|---|---|---|
| `search_trigger` must-search recall | 100% (8/8) | **100% (8/8)** |
| `search_trigger` must-not specificity | 100% (7/7) | **100% (7/7)** |
| `search_trigger_borderline` borderline recall | 30% (3/10) | **100% (10/10)** |
| `search_trigger_borderline` general-knowledge spec | 100% (8/8) | **100% (8/8)** |

The borderline gap is closed without over-searching stable knowledge. Pinned by
`test_router_wants_search_forces_search_despite_reasoner_heuristic` and
`test_router_wants_search_does_not_override_memory`.

## What is still unmeasured

- **Corroboration** — of the facts extracted from a search, how many are backed by
  ≥2 independent domains (the triangulation metric).
