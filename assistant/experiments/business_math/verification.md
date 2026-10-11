# Verification — business_math

## What was run

`experiment.py` on 2026-10-11: 3 models × 10 business problems × 3 trials = 90 calls
through the production compute path (`OllamaClient.execute_python` — the model writes
Python, the backend executes it, stdout is parsed) against local Ollama.

```
docker run --rm -v $(pwd):/app -w /app -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/business_math/experiment.py
```

## Non-mutation

No brain is opened; the compute path is called directly. No tools beyond the internal
`execute_python` subprocess. No network beyond localhost.

## Raw output

```
model                            correct   empty   median
dedicated math (incumbent)        30/30      0/30    2.96s
warm chat (qwen3.5:9b)            27/30      3/30    3.58s
warm utility (qwen3.5:4b)         30/30      0/30    3.13s
```

The chat model's three misses are **all the same problem** (the three-line invoice
subtotal), on all three trials, each with **empty stdout** — a *deterministic* failure
on one prompt shape, not run-to-run noise. The dedicated and utility models had no
empty-stdout cases and no errors.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — the chat model ≥ the dedicated model** | **fails** | 27/30 (0.90) vs 30/30 (1.00) |
| **H2 — the chat model's empty-stdout rate ≤** | **fails** | 0.10 vs 0.00 |
| **H3 — cost** | **achievable, but not via the chat model** | the **utility** model ties at 30/30 and is already warm |

## Falsification conditions, resolved

1. The chat model is correct on fewer pairs → **triggered** (27 < 30).
2. Its empty-stdout rate is higher → **triggered** (0.10 > 0.00).
3. Both hold → not reached for the chat model.

So the pre-registered candidate — **the chat model — is rejected**. But the plan also ran
the utility model as "an even cheaper candidate", and it **ties the dedicated model at
30/30 with no empty-stdout cases**, while being part of the already-resident warm set.

## The finding

- **The chat model is the wrong choice for compute, and reproducibly so.** Its
  conversational bias shows up exactly where the contract is strictest: on the
  three-line invoice prompt it answered instead of emitting the tool call, on every
  trial. That is the failure mode `docs/COMPUTE.md` names — and it is the same class as
  the Q8 miss, not a fluke.
- **The utility model is the right choice.** It is trained for structured output (its
  day job is JSON extraction), so it emits the tool call reliably — 30/30, zero empty
  stdout — and it is **already resident**. Reusing it costs **no new model load and no
  eviction**, where the dedicated model costs a 6.6 GB on-demand load that pushes a warm
  model out.
- **Reliability, not accuracy, was the axis.** Every model's arithmetic was exact (the
  backend computes it); the differences are entirely codegen — whether the model emitted
  a printing tool call.

## A caveat that needs its own measurement

Sharing the utility runner has a wrinkle: **Ollama reloads a model when the requested
context size changes.** Compute asks for `MATH_NUM_CTX` (16384); utility uses
`UTILITY_NUM_CTX` (4096). Pointing `MATH_MODEL` at the utility model without also
matching `MATH_NUM_CTX` to `UTILITY_NUM_CTX` would trade an eviction for a *reload* —
which is the cost this whole exercise is trying to avoid. **Not measured here.**

## Threats and limits (what remains)

- **30 (problem, trial) pairs is a probe.** 30/30 vs 30/30 is a tie, not a proof of one.
- **Business arithmetic only.** Hard/reasoning math was not asked — a 4B tying a 9B here
  says nothing about symbolic or multi-step reasoning.
- **Expected values come from a reference implementation in the harness**, not an
  external oracle.
- `execute_python` returns only stdout, so "no tool call" and "forgot to print" are
  merged into one signal.

## Addendum: the two final checks

### H4 — a `num_ctx` change reloads the model: **holds**

```
num_ctx=  4096  wall= 3.36s   loaded context_length 4096   size_vram 3.18GB
num_ctx=  4096  wall= 0.75s   (warm)
num_ctx= 16384  wall= 2.83s   loaded context_length 16384  size_vram 3.71GB   <- reload
num_ctx= 16384  wall= 0.78s   (warm)
num_ctx=  4096  wall= 2.89s                                                   <- reload back
```

Changing the requested context **reloads the model** — ~2.1 s of the ~3 s, with the loaded
`context_length` and `size_vram` both flipping. So if compute shares the utility runner,
`MATH_NUM_CTX` **must** match `UTILITY_NUM_CTX` (4096) or every compute call trades an
eviction for a reload — worse than the problem it was meant to solve.

### H5 — the dedicated model earns its keep on hard math: **VOID — the arm undermined itself**

The arm scored hard math by parsing numbers out of stdout. On the hard problems the numbers
it "saw" were unexplained small integers, and a 4B "beat" a 9B — which is the tell that the
metric was **not measuring the answer**. The scores reproduced on a second run, but
reproducible is not valid: the scoring was never shown to distinguish a wrong answer from a
traceback from a correct answer the parser missed.

**No model ranking is claimed from this arm.** It is recorded as a measurement that could
not support the conclusion drawn from it — and as the reason the harness now **records the
generated code** (`experiment.py`), so a re-run could settle it. It has not been re-run:
hard math is not what this project needs.

## What survives — and what does not

- **The business arm stands.** Its signal is empty-vs-non-empty stdout, which needs no
  parser, and two models scoring 30/30 proves the scoring found every answer — a parser
  missing real answers could not produce that.
- **The hard-math arm does not**, and the fleet decision did not rest on it: the dedicated
  model was retired for **tying** on business arithmetic while costing an on-demand load
  that evicts a warm model. The tie goes to the runner that is already resident.
