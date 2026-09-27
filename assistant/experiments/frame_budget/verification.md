# Verification: Memory Frame Budget (frame_budget_2026_09_27)

Status: COMPLETE. Conducted before `result.md` was written, against the
pre-registered plan in `plan.md` (commit `0b1c505`).

## 1. Was the manipulation real?

This is the check that mattered most, and it is the one the plan flagged as the
primary cheat risk. An earlier version of this experiment would have failed it.

| budget | frames requested | frames rendered | `manipulation_ok` |
|---|---|---|---|
| 0 | 0 | 0 | yes |
| 1 | 1 | 1.0 | yes |
| 2 | 2 | 2.0 | yes |
| 3 | 3 | 3.0 | yes |
| 5 | 5 | 5.0 | yes |
| 10 | 10 | 10.0 | yes |
| 20 | 20 | 20.0 | yes |
| 40 | 40 | 40.0 | yes |

`frames_rendered == frames_requested` holds in all 264 generations, asserted per
generation rather than assumed. `frames_rendered` is counted from the pool
actually passed to the renderer, and `memory_chars` rises monotonically
(0 → 3769 → 4573 → 4984 → 5376 → 6251 → 7879 → 10740), which is an independent
check that the prompt really did grow.

**A prior version of this experiment was a no-op and was caught.** The originally
requested variable, `max_frames_in_prompt`, never binds: retrieval returns exactly
3 frames for all 21 probe queries, so budgets 5/10/20/40 through that knob produce
byte-identical prompts. That would have yielded a clean, entirely false null
result. The variable was changed to `top_k_direct` before any generation was run.

## 2. Could the result be an artifact of the grader?

The grader is exact normalized substring matching against surface forms read from
the database. It has no access to the condition, no model in the loop, and cannot
be satisfied by a longer or more fluent answer.

**The screening step is the load-bearing defense and it did real work.** 14 queries
x 40 candidate gold facts were screened down to 22 facts across 11 queries. What
was rejected:

| rejection reason | facts | example |
|---|---|---|
| answerable at budget 0 (model prior knowledge) | 3 | `spotify`, `apple`, `amazon` — Groover's platforms, which any model knows |
| never produced even at budget 40 | 15 | `brodie_lane`, `zip`, `suite` — the label's mailing address |
| not in pool / not reached | remainder | see `target_ranks` in `result.json` |

Had the screen not run, `spotify`/`apple`/`amazon` would have scored at every
budget including 0, inflating the curve and making "more memory helps" look true
for the wrong reason. The 15 never-found facts are equally important: they are
cases where the model had the frame on screen and still did not surface the fact,
which is a salience problem that no frame budget fixes.

**Negative control passed cleanly.** Budget 0 recall = 0.000 across all 22
retained facts and 3 replicates. A grader that could be satisfied without memory
would have scored above zero here.

## 3. Could the effect be produced by something other than memory?

- **Retrieval is held constant.** One retrieval per query at `top_k_direct=40`,
  then truncated. Retrieval variance and the ~236ms search cost are outside the
  comparison entirely. Because the graph walk contributes 0 edges, truncating the
  pool is exactly equivalent to running production at `top_k_direct = budget`.
- **Episodes are held constant and were checked for leakage.** `recent_episodes`
  are included as production includes them. `shown`/`hidden` is computed against
  **frame text only**, so a fact answered from an episode would appear as
  `recall_when_hidden > 0`. It is 0.011 (2 of 183), so episodes are not leaking
  these answers.
- **Prompt construction is production code.** `build_system_prompt` and
  `format_memory_context` are called unmodified, at production temperature and
  think setting.

## 4. A bug this verification caught in the analysis itself

At budget 40 the 12000-char cap fires (73% of generations). Memory is appended
last, so the tail of the memory section is discarded before the model sees it.

The first implementation computed `shown` against the **pre-truncation** memory
text. That credited the model with facts from the discarded tail — inflating
`recall_when_shown` and overstating the high-budget results. It was fixed to
compute against the text actually sent, by locating the memory marker in the
assembled system prompt and measuring how many chars of it survived.

The smoke run that exposed this is not citable and was not committed
(`result_smoke.json` is written to a separate path and flagged `smoke_run: true`).

## 5. Threats to validity that remain

Stated rather than argued away:

1. **n = 22 facts across 11 queries.** Adequate for the 0.91 vs 0.011 separation,
   which is enormous, and not adequate for the smaller differences — the budget-2
   vs budget-3 tie, the budget-3 abstention blip, and the 10 → 20 → 40 decline are
   all within plausible noise at 3 replicates.
2. **Temperature 0.7 with 3 replicates.** A thinking-capable model at this
   temperature is not deterministic. The headline number is robust because it
   aggregates 345 and 183 trials; the per-query cells are not.
3. **Latency is cache-contaminated and is not used for any conclusion.** Each
   condition's prompt is generated three times and Ollama's cache serves all but
   the first call. In production the memory section changes every turn, so its
   prefill is real cost every turn. Reported `p50 ms` therefore *understates*
   production cost and its ordering across budgets is not trustworthy.
   `memory_chars` is the cost metric used for conclusions, and it is also what the
   12000-char cap scales with.
4. **`first visible` is a loose metric.** It scans frame text for the fact's
   surface form, so an incidental mention in a higher-ranked frame counts as
   visibility. This inflated `first` for `studio` and is why `studio` shows
   `first = 1` with recall 0.00 at budget 1. A slot-keyed measure would be tighter.
5. **11 queries is not the user's real query distribution.** These are questions
   with database-known answers, deliberately chosen to be answerable. Recall on
   vague, exploratory, or multi-hop questions is not measured and is likely lower.
6. **Generalization.** One brain, one day, one chat model. The 0.91/0.011 split is
   a property of this model reading this memory format; a different chat model
   could be materially worse at using what it is shown.

## 6. Did the pre-registered criteria resolve?

| criterion | result |
|---|---|
| rendered frames == requested, all conditions | PASS (264/264) |
| budget 0 recall ≈ 0 | PASS (0.000) |
| recall rises with budget and tracks visibility | PASS (0.000 → 0.697) |
| abstention falls with budget | PASS (0.485 → 0.000 by budget 5) |
| latency and prompt cost increase with budget | PASS on `memory_chars`; latency confounded and excluded |
| curve plateaus rather than continuing to rise | PASS (0.697 → 0.636 → 0.636) |
| no database mutation | PASS (digest identical before/after) |

## 7. Falsification conditions, checked

The plan named four ways this could produce a misleading result. All four were
checked against the raw data:

1. *Target in top 3 for every query* — refuted. Target ranks span 0–13.
2. *The 12000-char cap masks the budget* — partly true, and it is why the
   `shown`/`hidden` split is computed post-truncation. Budgets 1–5 truncate 0% of
   the time, so the rise from 0.000 to 0.515 in that range is unconfounded.
3. *Temperature noise exceeds the effect* — refuted for the headline (0.91 vs
   0.011). Not refuted for the small differences, which is declared in §5.1–5.2.
4. *Answers come from episodes, making frames irrelevant* — refuted.
   `recall_when_hidden` = 0.011, and it is computed against frame text only.

## Conclusion

The experiment is sound and its central result is robust: the model uses memory it
is shown 91% of the time and does not guess it 99% of the time it is not shown. The
budget recommendation (≈10, and no more) is supported but is a second-order
finding. The first-order finding is that the binding constraint is not prompt-side
at all — 877 live in-scope edges are reachable within 2 hops and the relevance gate
admits none of them, so production retrieval can only ever offer the model 3 frames
no matter what the budget is set to.
