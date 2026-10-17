# Experiment: does scoping the tool set help the scheduled-task path?

**Status: pre-registered.** Written before any trial. `result.md` after `verification.md`.

## The question

A scheduled task that reads a CSV, searches the web and writes a file is given **21 tool
schemas** — `compute`, `plan`, `think`, `run_scheduled_task`, `mark_essential`,
`upsert_association` and the rest — none of which it will use. That costs
`tool_schema_chars=19784` ≈ **4.9k tokens, 30% of the window, on every turn**, and it
shrinks the derived per-result allowance until results start being dropped
(`tool_results_dropped=2` was observed live).

Two separate questions hide in "should we use fewer tools per turn", and they have
different answers:

1. **Does it improve efficacy?** The observed failure was the model calling
   `search_file(vessels.csv, query='MAERSK ESSEX IMO 9632630')` — a substring search with a
   compound string, returning 114 characters — where `read_file` was meant. That is a
   **semantic overlap between two affordances**, not a symptom of there being twenty-one.
   Removing `compute` would not have prevented it.
2. **Does it reduce cost?** Almost certainly yes, and cheaply.

Designing one experiment for both would confound them, so this one has **three arms** and
reports efficacy and cost separately.

## Arms

| arm | tool set |
|---|---|
| **A — full** (control) | all 21, today's behaviour |
| **B — scoped** | the file/search/recall set a scheduled task can use; `compute`, `plan`, `think`, `run_scheduled_task`, `mark_essential`, `upsert_association` withheld |
| **C — scoped + clarified** | B, plus rewritten `read_file` / `search_file` / `glob` / `list_files` descriptions that say *when* to reach for each |

**C exists because B alone cannot distinguish the two mechanisms.** If B improves efficacy,
it could be the smaller list *or* the fact that a smaller list happens to remove the
confusable neighbours. C tests the explanation directly: same list as B, different
descriptions. If C beats B, the problem was ambiguity, not count — and that is the finding
that generalises to the *chat* path, where the full set must stay.

## Dependent variables

Efficacy, per trial (as in `ship_tracker_efficacy`, so the two are comparable):

- `outcome_class` — the same five classes, same definitions. **This is the primary.**
- `coverage`, `fields_complete`, `description_shape`
- `wrong_tool_read` — did it call `search_file` with a query that is not a search term
  (heuristic: a query containing a vessel name *and* an IMO/MMSI number, or > 4 words)?
  Recorded because it is the specific failure this experiment is motivated by.

Cost, per trial:

- `tool_schema_chars`, `peak_prompt_tokens`, `tool_results_dropped`
- `turn_total_ms`, and the count of LLM round trips

## Hypotheses and falsification

| # | Hypothesis | Falsified if |
|---|---|---|
| H1 | **B and C do not make efficacy worse** | `wrote_complete` is lower than arm A by ≥ 2 of 10 in either |
| H2 | **C reduces `wrong_tool_read` vs B** | C's rate is not lower than B's |
| H3 | **B and C cut the fixed cost materially** | `tool_schema_chars` falls by < 25% |
| H4 | **The saving reaches the results** | `tool_results_dropped` does not fall, *and* `peak_prompt_tokens` does not fall ≥ 10% |

**H1 is a non-inferiority test and is the one that matters.** A scoped set that makes the
agent worse is not a trade — it is a regression with a discount. H3 is expected to pass
trivially; it is in the table so that "it saved tokens" cannot be reported as the finding
if H1 fails.

## The bar, in advance

> If scoping the tool set makes `wrote_complete` worse, I will say the cost saving is not
> worth it and recommend keeping the full set on the scheduled path.
>
> If C does not beat B on `wrong_tool_read`, then tool *count* was never the problem, the
> descriptions were, and I will recommend fixing the descriptions for both paths rather
> than scoping either.

## Threats, named before the data

1. **The arms share a stochastic model and a non-deterministic web.** Arm-to-arm
   differences at 10 trials are dominated by that noise unless the effect is large. No
   p-values; counts, and the honest statement that a 1-trial difference is not a result.
2. **Arm C is a confound by construction** — it changes descriptions *and* inherits B's
   list. That is the point (it separates list from wording), but it means C is not a
   controlled single-variable change; the comparison B-vs-C isolates wording only because
   the list is held equal.
3. **Scoping may move the failure rather than remove it.** A withheld tool that the model
   wanted produces a *different* failure (it may narrate, or pick a worse tool), which
   `outcome_class` will show as `no_attempt` or `wrote_partial` — and both are worse than
   the cost saving is worth.
4. **The scoped set is a guess about what a task needs.** If a legitimate task needs
   `compute` (an arithmetic report) or `recall`, the experiment measures a broken
   configuration, not a cheaper one. Mitigation: the scoped set keeps every *read* tool
   (`list_files`, `read_file`, `search_file`, `glob`, `recall`, `search_episodes`,
   `web_search`, `fetch_url`) and every *write* tool (`write_file`, `edit_file`,
   `append_file`, `delete_file`, `rename_file`, `upsert_slot`) — it withholds only the
   meta-tools, which no scheduled task has ever called.
5. **Whose tools?** Scoping is per *path*, not per model. The chat path keeps all 21. A
   result here says nothing about chat, and must not be generalised to it.

## Ordering

**Run `ship_tracker_efficacy` first.** If the task is already `wrote_complete` in 10/10
after the nudge fix (`6f3d888`), then this experiment is a *cost* experiment — worth
running, but its headline would be tokens and latency, not efficacy. Running it first would
also risk attributing a nudge fix's benefit to a tool-set change, which is the confound
that makes a result unusable.
