# Result (audit): What the Conflict Ladder Actually Decides

Run 2026-10-01, against a throwaway database created in-process. Supersedes the
replay approach in `result.md`, which was inconclusive for an instrumentation reason.

## Why this replaced the replay

`result.md` recorded the honest outcome: the historical sample was 100%
unreconstructable because `slot_history` keeps a conflict's *values* but not their
*provenance*. The conflicts table now records the inputs, so this drives real writes
with controlled provenance and audits what the ladder decided — which is the question
the replay was trying to answer.

## Headline

| case | existing rel | new rel | status | winner | expected |
|---|---|---|---|---|---|
| user_statement_vs_search | 0.95 | 0.50 | **pending** | — | existing |
| search_vs_user_statement | 0.50 | 0.95 | auto_resolved | new | new ✅ |
| equal_reliability_recency | 0.50 | 0.50 | auto_resolved | new | new ✅ |
| no_provenance_at_all | — | — | auto_resolved | new | new ✅ |
| existing_higher_by_a_little | 0.65 | 0.60 | **pending** | — | existing |
| search_vs_web_fetch | 0.50 | 0.70 | auto_resolved | new | new ✅ |

**4/6 as scored, but the two "failures" are not decision failures.** Both are cases
where the existing side has higher reliability, and in both the existing value
**stood** — the slot kept it. The ladder decided correctly.

What is wrong is the **status**: both rows say `pending`, which reads as "undecided,
needs review". They were decided.

## The finding: `EXISTING_WINS` is applied but not recorded as a decision

`upsert_slot` branches on the resolution. `NEW_WINS` updates the slot and writes
`status='auto_resolved'`. The else branch — which covers `EXISTING_WINS` — inserts
`status='pending'`, writes `slot_history`, and leaves the value alone. The value is
right; the label is not.

So a decision the ladder *made* is indistinguishable, in the `conflicts` table, from a
decision it *declined to make*. That is the bug, and it has been quietly inflating the
apparent backlog:

```
279 pending conflicts
  256 (92%)  the slot still holds the existing value — the decision was applied
   23        the slot has since moved on (later writes, unrelated)
```

**92% of "pending" is not a queue. It is a set of applied decisions mislabelled as
open questions.**

## What this changes

**Plan D's premise was wrong, in the other direction from the plan.** The plan assumed
279 unresolved conflicts needed a reasoning pass. They do not: 256 were already
decided and correctly left alone. The genuine open set is at most 23, and those look
like slots that moved on by normal writing rather than unresolved disputes.

**The `grok` case is now understood.** A search result trying to overwrite a
user-stated fact (0.95 vs 0.5) is exactly `user_statement_vs_search`, and the ladder
held the user's value. The near-rename was not luck — it was the ladder working, and
the reason it *felt* like luck is that the record said `pending` rather than
`existing_wins`.

**This also revises `result.md`'s conclusion.** That file reported the earlier
"100% new-wins" reading as consistent with ties. That was right, and now we can say
more: the ties resolve to new by recency, and the non-ties resolve correctly. The
ladder is doing its job.

## Recommendation

**Do not build Plan D on this.** The 279-pending figure that motivated it was largely
a labelling artifact. The honest next step is the small one this experiment has
already earned:

- **Correct the status.** `EXISTING_WINS` should record `auto_resolved` with
  `resolved_value = existing_value` — as `manual_override_conflict` already does for
  the human path — so a decision and a deferral are distinguishable. Rows that are
  genuinely undecided then become a small, real queue.
- **Then re-count.** If the true open set is ~23 rather than 279, Plan D's cost/benefit
  is a different question, and probably not worth the labelling effort
  `conflict_reasoning_accuracy` requires.

## What this does not establish

- **Not that the ladder is well-tuned.** It discriminates correctly on the cases
  tested; whether 0.65 vs 0.60 *should* be decisive is a design question this does not
  answer.
- **Not that the 23 "moved on" rows are benign.** They need a look, and that is a
  query now rather than an investigation.
- **Not that Plan D is unwarranted in principle** — only that the backlog it was
  aimed at is not the backlog it appeared to be.
- **Synthetic cases, one process.** Six hand-built shapes, not observed traffic. The
  live 92% figure is independent evidence and agrees, but the case set is small.
