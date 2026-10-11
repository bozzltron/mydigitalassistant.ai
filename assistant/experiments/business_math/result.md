# Result — can the warm chat model replace the dedicated math model for business math?

**No — the chat model is worse, reproducibly. But the *utility* model ties the dedicated
one at 30/30 and is already warm, so the simplification is real — just not the way the
hypothesis proposed.**

| model | reliability | empty stdout | median | residency |
|---|---|---|---|---|
| dedicated math (`sorc/…-q4:9b`) | **30/30 = 1.00** | 0/30 | 2.96 s | on-demand — **evicts a warm model** |
| warm chat (`qwen3.5:9b`) | 27/30 = 0.90 | 3/30 | 3.58 s | already warm |
| **warm utility (`qwen3.5:4b`)** | **30/30 = 1.00** | **0/30** | 3.13 s | **already warm** |

## By hypothesis

| | Verdict |
|---|---|
| **H1 — chat ≥ dedicated** | **fails** (0.90 vs 1.00) |
| **H2 — chat's empty-stdout ≤** | **fails** (0.10 vs 0.00) |
| **H3 — cost** | **achievable via the utility model, not the chat model** |

## Falsification conditions, resolved

1. Chat correct on fewer pairs → **triggered** (27 < 30).
2. Chat's empty-stdout rate higher → **triggered** (0.10 > 0.00).
3. Both hold → not reached. **The pre-registered candidate is rejected.**

## The finding

- **The chat model fails where the contract is strict, and it fails the same way every
  time.** All three of its misses are the *same* prompt (the three-line invoice subtotal),
  all three trials, all with empty stdout — it answered in prose instead of emitting the
  tool call. A deterministic codegen failure, not noise.
- **The utility model is the right replacement.** Its job is structured extraction, so it
  emits the tool call reliably — 30/30, zero empty stdout — and it is **already part of
  the warm set**. Reusing it costs no new load and no eviction; the dedicated model costs
  a 6.6 GB on-demand load that pushes a warm model out and makes the *next* normal turn
  pay a reload.
- **Reliability, not accuracy, was the axis.** Every model's arithmetic was exact (the
  backend executes it); the only differences were codegen — whether a printing tool call
  was emitted at all.

## What this proves, and what it does not

- **Proved:** on business arithmetic, the warm utility model is as reliable as the
  dedicated math model and needs no extra residency.
- **Not proved:** that it holds on **hard/reasoning math** — a 4B tying a 9B on invoice
  totals says nothing about symbolic or multi-step work. The dedicated model may still
  earn its keep there.
- **Not measured:** the `num_ctx` interaction. Compute asks for `MATH_NUM_CTX` (16384);
  utility uses `UTILITY_NUM_CTX` (4096). **Ollama reloads a model when the requested
  context changes**, so pointing `MATH_MODEL` at the utility model without matching
  `MATH_NUM_CTX` to `UTILITY_NUM_CTX` would swap an eviction for a reload — the exact
  cost this is trying to remove.

## Recommendation

**For business math — inventory, invoices, tax — set `MATH_MODEL=qwen3.5:4b` *and*
`MATH_NUM_CTX=4096`**, so compute shares the warm utility runner without a reload. That
removes a 6.6 GB on-demand model from the fleet, keeps reliability at 30/30 on the
business set, and eliminates the warm-set eviction.

Two things to decide first, both cheap:
1. **Confirm the `num_ctx` reload** (a one-line measurement: run compute on the utility
   model with matching and mismatching `MATH_NUM_CTX` and watch `ollama ps`).
2. **Decide whether hard math matters.** If it does, keep the dedicated model behind an
   opt-in and route only business arithmetic to the utility model — but that is a second
   model, and the probe says the simple option is sufficient for the stated use case.

## Addendum: the final checks, and the decision

**H4 holds — the `num_ctx` match is required.** Requesting 16384 on a runner loaded at
4096 reloads it: 2.83 s against 0.75 s warm, with `context_length` and `size_vram` both
flipping (and 2.89 s to reload back). So the switch is only a win if `MATH_NUM_CTX` is
**4096**, matching `UTILITY_NUM_CTX`.

**H5 is void — the arm undermined itself.** It scored hard math by parsing numbers out of
stdout, and on hard problems it scraped unexplained values (a 4B "beating" a 9B was the
tell). **No model ranking is claimed from it** — see `verification.md`.

### Decision

**Set `MATH_MODEL=qwen3.5:4b` with `MATH_NUM_CTX=4096`** — on the **business arm alone**,
which is sound: its signal is empty-vs-non-empty stdout (needs no parser), and 30/30 twice
proves the scoring found every answer. The warm utility model **ties** the dedicated one on
business arithmetic and reuses a resident runner: no load, no eviction, and no reload once
the context matches. That removes the dedicated 6.6 GB on-demand model — **seven models to
six, and the minimum fleet is four** (see `docs/MODEL_SELECTION.md`).

### The retraction

An earlier version of this file claimed the dedicated model was "the worst of three" on hard
math and that compute is "unreliable on hard math (42–67%) for every model." **Both are
withdrawn** — they rested on a metric that was never validated. The harness now records the
generated code, so the question is answerable; it has not been re-run, because hard math is
not what this project needs.

## Addendum: the hard-math re-run, and what it actually measured

Re-run with the code captured. **Every failure is a `ModuleNotFoundError` or a removed
API** — `sympy` and `scipy` are not installed, and `np.irr` was removed in numpy 2.0. The
models wrote *correct* code that imported libraries the sandbox lacks.

So the arm measured **library choice, not maths** — and the ranking is inverted: the model
that reached for the proper tool most often scored worst. **The dedicated 9B's 4/12 is that
artifact, and there is still no valid evidence that it is worse at anything.**

**And that closes the loop on the earlier question.** The dedicated model was retired on a
**tie** (business arithmetic, 30/30 each) plus a **cost** (a load that evicted a warm model)
— and the cost was then removed by raising `OLLAMA_MAX_LOADED_MODELS` to 4. The hard-math
axis that could have justified keeping it has never been measured validly.

**The real bug this exposes is not a model choice.** `execute_python`'s system prompt tells
the model to "Use numpy, scipy, sympy, pandas, statistics as needed" — and **scipy and sympy
are not installed**. The prompt promises tools the sandbox does not have, so the model is
punished for following instructions. Fixing that (install them, or stop promising them) is a
prompt/image fix, not a fleet change, and it must come **before** any model comparison is
worth running again.
