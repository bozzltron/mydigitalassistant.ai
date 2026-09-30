# Verification: Does a Typed Decision Model Route Better? (decision_routing_value_2026_09_30)

Status: **COMPLETE.** Conducted before `result.md` was written, against the
pre-registered plan in `plan.md`.

Run: 2026-09-30, Ollama 0.35.0, 10 turns × 4 conditions. `result.json` holds the raw
per-turn records, including the exact prompts and the full probability vectors.

## 1. Isolation — was anything touched?

Isolated **by construction**, and demonstrated rather than asserted, because every
other experiment in this directory opens a brain and a reader will expect that here.

| Check | How established | Result |
|---|---|---|
| No database opened | Imports are stdlib + `httpx` only: `asyncio, json, os, statistics, time, dataclasses, pathlib, httpx`. No `MemoryStore`, no `aiosqlite`, no schema module. | **PASS** |
| No retrieval constructed | No `Retriever` import. | **PASS** |
| No tool executed | Condition A posts a chat completion and reads the model's *stated* route. No tool executor imported; no tool result produced anywhere in the run. | **PASS** |
| No search issued | No `WebSearchTool` import. `docker logs assistant-backend --since 20m` counted **0** Brave or SearXNG requests. No query text left the machine. | **PASS** |
| Live DB unchanged | `assistant.db` SHA-256 `96a625779fa18af2b0bd578bedd43365a30d23dca57b23554abf04cdd6093ceb` and mtime `1790798876` identical before and after. | **PASS** |
| Local inference only | `OLLAMA_URL=http://host.docker.internal:11434`, which is the host's `127.0.0.1:11434` — the same endpoint the app uses, per the project's hard rules. | **PASS** |

The DB digest is checked even though the module cannot open the database: "we read
what we think we read" is worth proving.

## 2. Was the manipulation real?

Every decision condition returned a typed `choice` with a `confidence`, on every
turn, with no errors:

| Condition | model | typed answers | errors | confidence range |
|---|---|---|---|---|
| B | `tev1:0.8b` | 10/10 | 0 | 0.16 – 0.93 |
| C | `tev1:latest` | 10/10 | 0 | 0.18 – 0.75 |
| D | `nimble:latest` | 10/10 | 0 | 0.28 – 0.98 |

No condition is scored on a model that failed to run, which was the stated risk.

The warm-up call is excluded from the latency statistics; the first *measured* turn
per model is reported in `result.json` so a cold start cannot hide inside the p50.

## 3. Did the control measure what it claims?

Condition A is a snapshot of `qwen3.5:9b`'s *stated* route under the real tool
framing:

- **10/10 turns returned valid JSON with a `route` key.** No parse failures, so no
  wrong answer is secretly a malformed one.
- A's prompt names the same four routes the decision question offers. The option
  sets are equal, so the comparison is not biased by an unequal menu. The full
  prompt is recorded in `result.json` for inspection.

## 4. Labels frozen before data

`plan.md` was committed as `2590e71` **before** the harness existed and before any
model ran. The labels in `plan.md` and `experiment.py` are identical. The decision
models are not the labellers — the labels are mine, which is carried into the
result's limitations rather than hidden.

## 5. Harness bugs, and what they would have cost

The model for this section is `graph_walk_yield/verification.md`, which caught a
silent constant that returned zero frames for 5 of 11 queries while producing a
complete, plausible result file.

Risks stated before the run, and their outcomes:

1. **`choice` criteria ordering (position bias).** If the endpoint biased toward the
   first criterion, `answer_from_content` would score artificially well on turns
   1–3 and the decision models' advantage would be an artefact. **Checked and
   cleared:** mean top-probability is 0.70–0.86, not saturated, and turn 10 (the
   negative control) is routed to `no_search` by two of three models — so the
   endpoint is discriminating, not defaulting.

   *Cost had it been real:* the headline result would have been entirely spurious.
   This is the single check that decides whether the numbers mean anything.

2. **Condition A option-set mismatch.** **Cleared** — both prompts offer the same
   four routes (§3).

3. **The 45-URL turn (turn 1) truncating.** If `state` were truncated, turn 1 would
   fail for input-length reasons and not routing reasons. **Cleared:** turn 1
   recorded 801–839 input tokens against 333–371 for the short turns, so the long
   turn was delivered in full and is the length it looks like.

4. **Latency including warm-up.** **Mitigated** by the explicit warm-up call, and
   the first measured turn is separately reported.

5. **One pull died silently mid-run.** `ollama pull tev1` exited and left only its
   wrapper shell alive, with the 9.5 GB blob stalled at a fixed size for 8 minutes.
   Re-running the pulls sequentially with visible output succeeded. *Cost:* none to
   the result — no condition ran against a missing model, because the pull was
   completed before the experiment started. *Would have cost:* a condition scoring
   every turn as an error, which §2's typed-answer count would have caught.

**One measurement weakness found after the run:** `TEV1:0.8B` routes 8 of 10 turns to
`answer_from_content`. That is not the same failure as the chat model's, but it is a
degenerate-looking preference and it makes B's 4/10 partly a default rather than a
judgement. It is reported as a finding, not smoothed over.

## 6. Non-mutation summary

Not applicable in the `graph_walk_yield` sense: no database was opened, so there are
no table digests to compare. The DB SHA-256 and mtime check in §1 stands in for them,
and the structural argument (no store import) means non-mutation is guaranteed rather
than merely observed.
