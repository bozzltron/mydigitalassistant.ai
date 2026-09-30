---
date: 2026-09-30
status: active
estimated_hours: 11
---

# Plan A — Triangulation: user content is a source, not a prompt

## Objective

The assistant constructs an accurate model of the user's world by triangulating
**the user and the internet as sources** (AGENTS.md → Project goal). This plan fixes the
demonstrated case where it did the opposite: the user supplied 45 URLs, and the assistant
answered from the web while never touching what the user gave it.

**Scope: capture + reconcile.** Error-correction is handled by the conflict system
(Plan D), not by a new detector here — see "What this plan does not do".

**Depends on Plan B** (write-path hygiene). Phase 1 creates frames, and `apply_extraction`
currently permits empty frame names (that is what produced frame 4387). Shipping A first
would make every paste a fresh opportunity to create a blank frame, and A's own acceptance
test could not hold. B lands first.

## The failure as two triangulation errors

1. **The user's source was never registered.** `episode 2653.frame_ids = []`. A 3.5 kB
   first-person artefact — the highest-authority source about the user's own world —
   produced no memory at all. Unregistered content cannot participate in any later
   reconciliation.
2. **The user's source was silently outranked by the internet.** The router decided
   `wants_search=true` from the *intent* of the sentence, and both follow-up turns
   answered from fresh Brave queries while the user's list sat unread. Search was not
   enriching the artefact; it was replacing it.

Evidence (live brain, 2026-09-30, `assistant-backend`):

```
episode 2653  user   "Here are all my manual submission links... rank them by which
                      would be most impactful to mozworth" + 45 URLs   frame_ids=[]
09:11:52      turn_pregen: routing_ms=6607 recall_ms=6607 plan_ms=2699 extraction_ms=2699
09:12:17      web_search x3 (WPRB, Rock Rage Radio, KEXP)     turn_total_ms=60530

episode 2655  user   "Sort the whole list of links in order of impact for mozworth."
09:13:45      "Search vetoed by router for storage-style turn"
09:14:07      tool loop called web_search anyway              turn_total_ms=62536

episode 2657  user   "I gave you a list of links to sort in the conversation."
episode 2658  assistant: SubmitHub / Groover / PlaylistPal — none in the user's list

frame 4387:  name=''  slots=0  associations=0  created 14:13:45
```

Latency corroborates the diagnosis: 60.5 s and 62.5 s turns that each spun up a fresh
search instead of transforming the content the user had already given.

## Phases

### Phase 1 — Register user-supplied content as a source (~5 h)

The user's content must become a durable, retrievable, attributable memory object, or it
cannot be triangulated later. This is **general**, not a URL special case — a pasted
email, a table, or a draft evaporates exactly the same way frame 4387 did.

1. **Detect user-supplied content as a class.** In the existing concurrent extraction
   slot (no new sequential LLM call): a model-free signal (≥5 bare URLs) plus the
   utility model's judgment for other artefact shapes.
2. **Register it with provenance.** A frame with `source_type="user_supplied"`,
   `source_reliability` high (user-stated), raised `priority`, and `source_episode_id`
   pointing at the turn that supplied it. Order preserved as given — a ranking pass must
   not lose it.
3. **Always store the raw artefact, independent of the model.** If detection fires and
   extraction produced nothing, write it directly. This is a durability fallback for
   *data*, not a scripted user-facing response, so it does not violate model-first.
4. **Render it at full length**, like a file frame — never as a 240-char episode digest
   (`max_episode_digest_chars`), which structurally cannot carry a paste.

**Acceptance:** after a paste, `episode.frame_ids` references a frame with
`source_type="user_supplied"` containing every URL in original order, and
`extraction_summary.slots_applied > 0`.

### Phase 2 — Reconcile: user content is the subject, search may enrich (~4 h)

When the request is to transform user-supplied content (sort, rank, summarise, compare,
rewrite), that content is the subject. Search enriches it; it never replaces it.

5. **Encode the authority rule where the model can act on it.** `build_system_prompt`
   gets a stable section stating the AGENTS.md rule. Stable placement keeps the prompt
   cache intact — the rule sits in the prefix that already reuses KV across turns.
6. **Stop search pre-empting a transform request.** In `orchestrator.chat`, when the
   intent class is transform-the-user's-content and registered user content exists,
   search is enrichment, not the primary path — it must not become the answer's subject.
7. **Make the way back visible.** Add "use when the user refers to something earlier in
   this conversation" to the `recall` and `search_episodes` tool descriptions. At 09:13
   the list *was* inside the 6-turn verbatim history window (`limit=7`,
   `prior_turns[:-1]`); the model simply had no tool description pointing at it.

**Acceptance:** a replay of 2653 → 2655 enumerates and sorts the user's actual list;
`web_search` is not the only tool called; latency is comparable to a normal turn.

## What this plan does not do

- **No reconciliation detector.** An earlier draft of this plan added a Phase 3 that would
  check whether an answer contradicted a higher-authority source. That is dropped:

- It would be a parallel mechanism to the existing conflict path. The right home for
  "a higher-authority source disagrees" is the conflict system, which already records
  disagreements with provenance and audit (`slot_history`).
- Plan D is where conflicts are reasoned about. Adding a detector here would mean two
  systems answering the same question.

**No retrieval re-tuning.** The `daily_run_*` frames acting as a retrieval magnet is a
real observation but it is a *measurement question*, not a plan phase. It moves to
`assistant/experiments/daily_run_retrieval_magnet` (appendix below).

## Requirements

**Functional**

| # | Requirement | Phase |
|---|---|---|
| R1 | User-supplied content (a paste of ≥5 URLs, or an equivalent artifact class) is registered as a memory frame. | 1 |
| R2 | The frame carries `source_type="user_supplied"`, high `source_reliability`, raised `priority`, and `source_episode_id` pointing at the supplying turn. | 1 |
| R3 | Item order is preserved exactly as supplied — a ranking pass must not lose it. | 1 |
| R4 | If detection fires and extraction produced nothing, the artifact is written anyway (durability fallback). | 1 |
| R5 | Registered content renders at full length, never as a 240-char episode digest. | 1 |
| R6 | The system prompt carries the authority rule: when a request transforms user-supplied content, that content is the subject; search enriches, never replaces. | 2 |
| R7 | A transform-intent turn with registered user content does not let search become the answer's subject. | 2 |
| R8 | `recall` and `search_episodes` tool descriptions state they are to be used when the user refers to something earlier in the conversation. | 2 |

**Non-functional**

| # | Requirement |
|---|---|
| N1 | No new sequential LLM call on the hot path. Phase 1 hangs off the existing concurrent extraction slot. |
| N2 | The authority rule sits in the stable prompt prefix, so the prompt cache is not invalidated. |
| N3 | No templated user-facing response. The model writes every answer; the plan only guarantees the source data exists. |
| N4 | User content stays local. The authority rule must *reduce* searches, not increase them. |
| N5 | No cloud LLM; all inference local. |

**Constraints**

- **Depends on Plan B.** A creates frames; B makes blank names impossible. Shipping A first
  would make each paste a fresh chance to create a blank frame, and A's own acceptance test
  could not hold.
- **Do not add a second conflict mechanism.** Named explicitly because the earlier draft did.

## Dependencies and ordering

- **Depends on Plan B** (hard).
- **Blocks nothing**, but the `daily_run_retrieval_magnet` experiment is spun from it.
- **Independent of Plans C, D, E.**
- Within the plan: Phase 1 → Phase 2.

## Test strategy

Regression tests, each named for the bug it prevents:

- `test_user_supplied_content_registered.py` — a paste produces a frame with
  `source_type="user_supplied"`, provenance to the supplying episode, order preserved,
  `frame_ids` non-empty.
- `test_paste_fallback_write.py` — with a stubbed extractor returning nothing, the
  durability fallback still stores the artefact (the "small model had a bad day" path).
- `test_user_content_is_subject_not_search.py` — transform intent + registered user
  content: the prompt carries the authority rule and search does not become the answer's
  subject.

Gates: `pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py`,
then the full suite, then `ruff check .`.

## Principle alignment

| Principle | How |
|---|---|
| Lean on the model | Capture is model-driven first; the deterministic write is a durability fallback for *data*, not a scripted user-facing response. |
| No templated responses | The model still writes every answer. |
| Speed-first | No new sequential LLM call: Phase 1 hangs off the existing concurrent extraction slot. Phase 2 is prompt text and routing. |
| Stability | One regression test per defect, named for the bug it prevents. |
| Clean ship | This plan's removal target is the reconciliation detector it originally carried — recorded above so it is not re-added. |
| Safety & Privacy | User content stays in the local brain; the authority rule *reduces* unnecessary trips to search. |

## Rollback

Phase 1 adds a source type and a persistence path behind a detection threshold; Phase 2 is
prompt text plus one routing condition. No schema migration. Both revert with
`git revert`.

## Appendix — observation moved to an experiment

`daily_run_*` frames have near-identical `date`/`tasks_run`/`status` slots, cluster
tightly in embedding space, and appeared in the prompt on the 09:13 submission-links turn
(`Retrieved daily run frames: ['daily_run_2026_08_28', 'daily_run_2026_09_16']`) where
`job_postings_monitor` and `model_evaluation` are unrelated content.

Hypothesis: they act as a magnet for short keyword queries. This needs the project's
experiment treatment (pre-registered, read-only against a brain copy, falsification
conditions) before any ranking change ships — see `graph_walk_yield` for the pattern.
Not a plan phase.
