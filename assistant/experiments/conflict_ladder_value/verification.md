# Verification: Is the Conflict Ladder Doing Anything? (conflict_ladder_value_2026_10_01)

Status: **COMPLETE.** Conducted before `result.md` was written, against the
pre-registered plan in `plan.md`.

## 1. Isolation

| Check | How established | Result |
|---|---|---|
| Ran against a copy, not the live DB | `EXP_DB=/exp/assistant.db`, bind-mounted from `/tmp/clv_exp` | **PASS** |
| Copy is what we think it is | `sha256` of the copy `217f9a94816dd202…` identical to the live `/app/data/assistant.db` at snapshot time | **PASS** |
| Live volume not mounted | The experiment container mounts `/app` (repo) and `/exp` (copy) only; `/live` does not exist in it | **PASS** |
| No writes | The module imports `aiosqlite_connect` for reading only. No `create_*`, no `upsert_*`, no `UPDATE`/`INSERT`/`DELETE` anywhere in it. | **PASS** |
| Live DB unchanged after | Re-checked at the end: digest `217f9a94816dd202…`, unchanged | **PASS** |
| Local only | No network calls; pure arithmetic over the copy | **PASS** |

`experiments/preflight.py` was **not** run. It checks for a restorable
`.assistant-brain` backup, which this repo cannot produce — the portable-brain
export documented in AGENTS.md does not exist in the code (noted separately). The
checks it performs were done by hand above and are recorded rather than skipped.

## 2. Was the manipulation real?

The manipulation is "supply honest provenance vs supply what production supplies".
The harness's condition A supplied `new_source_reliability=None` (production) and
condition B supplied a value. Both ran without error across 556 cases, so the
*mechanics* worked.

What failed is whether the inputs meant what the harness assumed — see §4.

## 3. Harness bug, and what it cost

**The central finding of this verification.** The harness read each side's
`source_reliability` from the slot row as it exists **now**, and treated it as the
provenance at conflict time. It is not: the slot holds the *current* value, and a slot
can change many times after a conflict is recorded.

Demonstration:

```
conflict 1:   existing='Assistant'  new='Luna'  resolved='Luna'
slot now:     full_name='Echo'  source_type=user_correction  reliability=0.99
slot_history: 'Echo'→'grok' (revise), 'Echo'→'Echo' (expand) ...
```

The harness attributed `Echo`'s 0.99 to `Assistant`, three changes earlier.

**Cost had it gone unreported:** the result would have claimed the ladder is bypassed
in 68% of decisions — a dramatic, publishable-looking, entirely false finding that
would have driven a rewrite of `resolve_conflict` and possibly all of Plan D. The
only reason it did not is that `A reproduces production: 367/277` is arithmetically
impossible (more agreements than checkable cases), which forced a look.

This is **plan threat 1, written before the run and then ignored**: *"Replay is not
the original write. Reconstructing the inputs of a past conflict is an
approximation."* The threat was recorded and the harness was built as though it did
not apply. That is a process failure worth naming: writing a threat down is not the
same as designing against it.

## 4. Can the inputs be reconstructed at all?

No. `slot_history` holds a matching `old_value`/`new_value` pair for **556 of 556**
conflicts, so the *values* are recoverable — but the table has no provenance column:

```
slot_history columns: id, slot_id, frame_id, slot_key, old_value, new_value,
                      reason, source_episode_id, timestamp
```

`source_reliability`, `confidence`, and `priority` at decision time are **not
retained**. Reconstruction is impossible for 100% of the sample, which is plan
falsification condition 4 exactly, and it is the outcome reported.

## 5. Hypotheses frozen before data

`plan.md` carries no commit of its own (it was written before this session's work on
the plan set). The hypotheses, the ≥95% / ≥20% bars, and the four falsification
conditions are unchanged in that file. The relevant one — condition 4 — was met, and
the result is reported as inconclusive rather than reinterpreted.

## 6. One premise of the plan was wrong

The plan asserted: *"extraction writes every slot with `source_reliability=null` →
0.5"*. Measured on the copy:

```
search            n=2197  null_reliability=0
scheduled_task    n=124   null_reliability=0
web_fetch         n=67    null_reliability=0
user_correction   n=16    null_reliability=0
```

The main extraction paths **do** write provenance. The premise was inherited from the
earlier (correct) observation that `revise()` passes `new_source_reliability=None` —
which is true of the *write path* but does not mean the *stored* slots lack it.

## 7. Non-mutation summary

Live DB digest `217f9a94816dd202599e4f6a8a23e57020f15c09f89089659cd68ca001b23acc`
before and after: identical. The copy was not re-digested after the run; the module
performs no writes, which is structurally verifiable from its imports and body, and
the live DB check is the one that matters.

---

## Addendum: the audit run (2026-10-01, after provenance recording)

The replay was inconclusive (§4), so a second run audits *live* decisions instead of
replaying historical ones. Recorded here because it is a different method and needs
its own checks.

| Check | How established | Result |
|---|---|---|
| No user data touched | Writes go to a `tempfile.mkdtemp()` database created in-process; `init_db` on that path. The brain is never opened. | **PASS** |
| The manipulation is real | Each case writes a controlled existing value, then a controlled new value, and reads back the conflict row the store recorded. | **PASS** |
| The audit reads what was recorded | `existing_source_reliability` and `new_source_reliability` come off the returned `Conflict` model, not from the case definition — so a write that dropped them would show as `None`, not as a pass. | **PASS** |
| Scoring is stated in the script | `correct()` is defined in `audit.py`: a tie may resolve to new (recency) or stay pending; a non-tie must match the higher-reliability side. | **PASS** |

**The two "failures" are not measurement failures.** Both are the higher-reliability
existing side staying in place with `status='pending'`. The value was preserved, so the
decision was made; the status is what misrepresents it. That distinction is the
finding, and it is corroborated independently by the live count (256 of 279 pending
rows still hold the existing value).

**Threat not resolved:** the case set is six hand-built shapes, not observed traffic.
The live 92% figure is independent evidence that agrees with it, but neither alone
establishes the behaviour across the real distribution.
