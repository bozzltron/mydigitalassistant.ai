# Verification: Does a Typed Decision Model Route Better? (decision_routing_value_2026_09_30)

Status: **IN PROGRESS — this file is completed before `result.md` is written.**

Verification is conducted against the pre-registered plan in `plan.md`, and the
results are not to be written until the checks below are filled in.

## 1. Isolation — was anything touched?

This experiment is isolated **by construction**, not by copy, and that must be
demonstrated rather than asserted, because every other experiment in this directory
opens a brain.

| Check | How it is established | Result |
|---|---|---|
| No database opened | The module imports no `MemoryStore`, no `aiosqlite`, no schema module. Verify by inspection of `experiment.py` imports. | _pending_ |
| No retrieval constructed | No `Retriever` import. | _pending_ |
| No tool executed | Condition A posts a chat completion and reads the model's *stated* choice; no tool executor is imported and no tool result is produced. | _pending_ |
| No search issued | No `WebSearchTool` import. Brave and SearXNG are never contacted; no query text leaves the machine. Verify from the Ollama server log that only `/api/chat` and `/v1/systemone` were received. | _pending_ |
| Live DB unchanged | The experiment does not know the DB path. Confirm mtime and SHA-256 of `assistant.db` are unchanged across the run. | _pending_ |
| Local inference only | `OLLAMA_URL` default `http://127.0.0.1:11434`. Record the actual base URL used. | _pending_ |

The `assistant.db` digest check is done even though the module cannot open it —
"we read what we think we read" is worth proving rather than reasoning about.

## 2. Was the manipulation real?

For each decision condition, the endpoint must have returned a typed `choice` with a
`confidence` — not an error, not a fallback.

| Condition | model | answers returned | errors |
|---|---|---|---|
| B | `tev1:0.8b` | _pending_ | _pending_ |
| C | `tev1` | _pending_ | _pending_ |
| D | `nimble` | _pending_ | _pending_ |

If any condition returned errors for every turn, that is an API or model-availability
finding and it is reported as such — **not** scored as low accuracy. Scoring a model
that never ran as "0/10 correct" would be a false result.

The warm-up call is excluded from latency statistics; confirm it is not in the
measured sample.

## 3. Did the control measure what it claims?

Condition A must be a snapshot of the chat model's *stated* route under the real tool
framing, and it must be parseable. Record:

- how many of the 10 turns returned valid JSON with a `route` key;
- how many returned an unparseable or missing route (a parse failure is not a wrong
  answer and must be reported separately);
- whether A's prompt names the same four options the decision question offers, since
  an unequal option set would bias the comparison. (It does, by construction; confirm
  from the recorded prompt in `result.json`.)

## 4. Labels frozen before data

The labels in `plan.md` and `experiment.py` must be byte-identical, and both must
predate the run. Verify against git history: the commit containing `plan.md`
(`2590e71`) predates the commit containing results.

## 5. Harness bugs, and what they would have cost

The model for this section is `graph_walk_yield/verification.md`, which found that a
silently-wrong constant returned zero frames for 5 of 11 queries while producing a
complete, plausible result file.

Known risks in this harness, stated before the run:

1. **`choice` criteria ordering.** If the endpoint biases toward the first criterion,
   `answer_from_content` would score artificially well on turns 1–3. Mitigation:
   check the `probabilities` distribution — a genuine decision should not be ~1.0 on
   every turn.
2. **Condition A option-set mismatch.** A is asked for four routes with different
   wording than the decision criteria. Mitigation: record A's full prompt in
   `result.json` for inspection.
3. **The 45-URL turn is long.** If the decision model truncates `state`, turn 1 may
   fail for input-length reasons rather than routing reasons. Mitigation: record
   `usage.input_tokens` per turn and check turn 1 against the rest.
4. **Latency includes model warm-up.** Mitigated by the explicit warm-up call, but
   the *first measured* turn per model is still reported separately so a cold start
   cannot hide in the p50.
5. **Label authorship.** I authored both the turns and the labels (plan threat 2).
   This is not fixable within the harness and is carried into the result's
   limitations rather than hidden.

Bugs found during the run are recorded here with their user-visible cost, in the
style of the two above.

## 6. Non-mutation summary

_pending — table digests are not applicable (no DB opened); the DB digest and mtime
check in §1 stands in for them._
