# The prompt response pipeline

One turn, start to finish: what runs, in what order, which model does it, and which parts
are optional.

## The shape

`Orchestrator._run_turn()` is the **only** implementation of the loop. `chat_stream()`
(SSE), `chat()` (the CLI adapter) and `run_scheduled_task()` are all consumers of it — a
feature added to the loop reaches every path by construction. Do not add a second loop;
three copies drifted before (see `test_stream_parity.py`).

## Stages, in order

The `stage` events on `/chat/stream` are the user-visible names of these steps.

| stage | what happens | model |
|---|---|---|
| `correcting` | a flagged correction is parsed, validated (corroboration), applied | utility |
| `routing` | classify the turn; decide `wants_search` | utility (heuristic first) |
| `recall` | embed the query → sqlite-vec → graph walk → memory context | embedding |
| `searching` | only when the router wants it (or a scheduled task forces it) | search backend — no LLM |
| `reasoning` | plan: answer / search / correct / compute; may escalate | utility, or MAX when escalated |
| `learning` | extract frames/slots/associations, resolve conflicts, store | utility |
| `computing` | the `compute` tool — the model writes Python, the backend executes it | `MATH_MODEL` |
| `responding` | the tool loop: tool calls, then the final answer | `TOOLS_MODEL` (= chat) |

Two LLM groups run **concurrently**, so each costs its slowest member, not its sum:
**routing ‖ recall ‖ the action judgement**, then **plan ‖ extraction**. They are
sequential *with respect to each other*, so a new blocking call added to either group
costs every turn. The measured cost is logged per turn — `turn_pregen` (the dead time
before generation) and `turn_timings` (including `ttft_ms`). Read them before changing
the hot path; `test_turn_timings.py` pins them.

## The guards (model-gated, best-effort)

A turn can end *without doing what the user asked* — the model announces an action and
calls no tool. The loop defends against that with a bounded nudge, and both judgements are
**best-effort**: a missing model returns "unknown" and nothing nudges, never a guess.

| guard | when it fires | model | default |
|---|---|---|---|
| **action nudge** | the request needs an action and the turn ends with **no tool call** | `DECISION_MODEL` = `tev1:0.8b` | **on** |
| **completion nudge** | the request needed an action, a tool ran, and the turn still ends without doing it | `DECISION_COMPLETION_MODEL` = `nimble` | **off** |

The completion nudge is **off by default**: it fires only on a turn that already needed an
action, ran a tool, and *still* left it undone, and `experiments/nudge_bound/` found the
**first** nudge fired on **0 of 10** live write-class turns — so the second is rarer still.
It also needs a ~9.5 GB model that `tev1:0.8b` cannot replace (measured: no separation).
Set `DECISION_COMPLETION_MODEL=nimble` to re-enable it. The nudge is bounded to one round
per turn, and emits an `acting` stage so a self-correction is *seen*, not just felt as
latency.

## Required models vs optional tiers

Each optional tier costs a model load. **The minimum fleet is four models**
(`docs/MODEL_SELECTION.md`):

| role | model | required? |
|---|---|---|
| chat + tools | `qwen3.5:9b` | **yes** — one runner, shared context |
| utility (routing, extraction, summarisation) | `qwen3.5:4b` | **yes** |
| embedding | `qwen3-embedding:0.6b` | **yes** |
| action judgement | `tev1:0.8b` | **yes** — it fixed a real "announced the file but never wrote it" bug |
| compute | `qwen3.5:4b` (the utility runner) | yes if math is wanted; empty disables `compute` |
| completion judgement | `nimble` | **no** — off by default |
| escalation | `qwen3.8:27b` | **no** — empty falls back to thinking-mode on chat |

**Emptying a role disables its feature cleanly** rather than failing — that is the
hardwiring mechanism. `MATH_NUM_CTX` must equal `UTILITY_NUM_CTX` when compute shares the
utility runner (`docs/COMPUTE.md`).

## Where the answer comes from

- **Search results** are retrieval-only unless extraction promotes them to facts; a
  `**Sources:**` footer is appended when they are used.
- **`compute`** answers come from the *executed process's stdout*, never from the model
  stating a number (`docs/COMPUTE.md`).
- **Memory** answers cite frames/slots/episodes, and conflicts are resolved by the
  confidence ladder with both values kept in `slot_history`.

## Related

- `docs/CONTEXT_THROUGHPUT.md` — windows, caps and the arithmetic behind the loop's
  context budget.
- `docs/MODEL_SELECTION.md` — the fleet, the roles, and what was measured and rejected.
- `docs/COMPUTE.md` — the write-and-execute path.
- `assistant/AGENTS.md` — the loop's rules as the codebase's own guidance.
