# Experiment: does the agent still know what was said 20 messages ago?

**Status:** pre-registered before data collection. `result.md` is written only
after `verification.md`.

## Question

A live session holds **802 episodes**, but `verbatim_history_turns = 6` — the
prompt carries the last six messages (three exchanges). The user reports that
earlier parts of a conversation "get lost". Does the model, in fact, answer
correctly about facts from earlier in the same session? And does **injecting the
stored session summary** improve that?

The summary exists and is stored, but `_conversation_summary()` says it is
"transparency, not context — never added to the prompt". That is the thing this
run measures: the summary is 509 characters for 802 turns, and it is currently
dead weight.

## Hypotheses

- **H1 (baseline recall).** With six verbatim messages and retrieval, the model
  answers correctly on fewer than 80% of old-fact questions. Below 0.80 means the
  reported loss is real and reproducible.
- **H2 (summary injection).** Appending the session summary to the prompt raises
  recall on facts the summary contains by ≥ 0.20, and does not lower it on facts
  it does not contain.
- **H3 (cause).** The failures are retrieval misses, not refusals: the model
  should rarely say "I don't have that", and more often answer with something
  plausible but wrong or incomplete.

## Conditions

| | prompt |
|---|---|
| A (`baseline`) | current production: last 6 messages + retrieved memory |
| B (`summary`) | A, with `## This conversation so far\n<summary>` prepended to the memory section |

Retrieval is computed **once per question** and reused for both conditions, so
the only difference is the summary text.

## Questions (session `conv_f8df1353725c`, 802 episodes)

| # | question | gold token | where the fact lives |
|---|---|---|---|
| 1 | What total profit did we calculate for the Gold Rush vinyl? | `1,008` | session summary |
| 2 | Which platforms did we prioritise for Mozworth's outreach? | `submitfinder` | session summary |
| 3 | How many Instagram followers does Mozworth have? | `296` | episode ~1362, **not** in the summary |
| 4 | How did we split the email broadcast segments? | `non-austin` | episode ~200, **not** in the summary |
| 5 | What release date did we set for "Why Not"? | `oct 1` / `october 1` | episode 911 (first exchange) |

Questions 1–2 test the mechanism (does the summary reach the model and get used).
Questions 3–5 test retrieval with or without the summary — they are expected to be
unaffected by B, and a drop there would mean the summary crowds out retrieved
memory.

## Metric and grading

- `recall` — the gold token appears in the answer, case-insensitive, commas
  stripped (`1,008` ≡ `1008`). Deterministic, no judge.
- `declines` — the answer says it does not have the information ("don't have",
  "no record", "not in my memory", "can't find"). Recorded because a refusal is a
  different failure from a confident wrong answer, and only one of them is honest.
- `prompt_tokens` per answer, from the model.

## Controls

- Same session, same stored memory, same retriever, same chat model,
  `temperature = 0.7`, `think = False`.
- Search and tools are **off**: this measures what the prompt carries, not what a
  tool could fetch.
- 3 replicates per question per condition, conditions interleaved and
  order-rotated per replicate.
- **Read-only against prod.** The harness opens the store for reads only and
  never goes through the orchestrator, so no episode or frame is written. No copy
  is needed because nothing mutates.

## Falsification (decided before the run)

- **H1 null** if baseline recall is ≥ 0.80. The conversation is not losing
  context in the prompt; the report is about something else (the UI's 50-message
  restore, a new session id, or phrasing).
- **H2 null** if B does not beat A on questions 1–2 by ≥ 0.20, or if B lowers
  recall on 3–5.
- A refusal rate above 0.5 would mean the model knows it does not know — a
  different (better) failure than a confident wrong answer, and it changes the
  recommendation rather than supporting it.

## Success criteria

- [ ] Retrieval is identical across A and B for every question (asserted on the
      rendered memory text)
- [ ] Baseline recall is measured per question, not just as an average
- [ ] Summary questions and episode questions are reported separately
- [ ] No database write occurs

## Run

```bash
docker compose -f docker-compose.prod.yml exec -T assistant \
  python -m assistant.experiments.conversation_recall
```
