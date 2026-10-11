# Experiment: can the warm chat model replace the dedicated math model for business math?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The `compute` tool has the model write Python and the backend execute it, so the
**arithmetic is exact by construction** — the reliability question is **codegen**: does
the model emit a runnable expression that *prints* its answer? Business use (inventory,
invoices, tax) needs that **every time**. The dedicated math model
(`sorc/qwen3.5-claude-4.6-opus-q4:9b`, 6.6 GB) costs an on-demand load that **evicts a
warm model** (see `docs/MODEL_SELECTION.md`); the chat model (`qwen3.5:9b`) is already
warm.

The earlier `math_model_value` probe ran a chat arm on *generic* problems and it missed
1/8 (empty stdout — the same failure class as the Q8 build). That was a floor, not a
decision, and it was **single-shot**. This probe asks the question directly, on
**business** problems, with repetition.

## Hypotheses

- **H1 (reliability — binding).** The chat model is correct on **≥** as many business
  (problem, trial) pairs as the dedicated math model.
- **H2 (the print contract).** The chat model's **empty-stdout** rate is **≤** the
  dedicated model's.
- **H3 (cost).** Replacing the dedicated model removes a 6.6 GB on-demand load and its
  eviction of a warm model, with no new residency.

## Falsification conditions

1. The chat model is correct on **fewer** (problem, trial) pairs → keep the dedicated
   math model.
2. Its empty-stdout rate is **higher** → keep the dedicated math model.
3. Both hold → alias `MATH_MODEL` to the chat model (or make `compute` fall back to the
   tools model when `MATH_MODEL` is empty) and drop a model from the fleet.

## Variables

- **Models:** `sorc/qwen3.5-claude-4.6-opus-q4:9b` (dedicated, incumbent); `qwen3.5:9b`
  (warm chat); `qwen3.5:4b` (warm utility — an even cheaper candidate).
- **Problems:** 10 business-flavoured — invoice line totals, subtotal, sales tax,
  invoice total, stock movement, inventory valuation, gross margin, discount, split bill
  with tip, loan interest. Each carries a computed expected value and an explicit
  tolerance (money ±0.02, percentages ±0.05, counts exact).
- **Trials:** 3 per problem. Reliability is consistency, not a single shot.

## What counts as evidence

**Reliability per model is the number** — correct (problem, trial) pairs over 30. The
empty-stdout rate separates "the model refused" from "the model wrote a wrong formula".
Latency is secondary (the tool is not on the chat hot path).

## Method

Reuse the production compute path (`OllamaClient.execute_python`) exactly as
`math_model_value` does: the model writes the tool call, the backend executes, stdout is
parsed. No brain, no tools.

```bash
python assistant/experiments/business_math/experiment.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- **10 problems × 3 trials is a probe**; it can show a gap this large, not certify a rate.
- **`execute_python` returns only stdout**, so "no tool call" and "forgot to print" merge
  into one failure signal — the generated code is not inspected.
- **Expected values come from a reference implementation in the harness**, not an
  external oracle; a wrong reference would fail every model equally.
- `temperature=0.0`, but codegen still varies run to run; 3 trials sample that.
- **Hard math is still unmeasured.** These are business-arithmetic problems, not
  reasoning problems; a win here does not claim the 27B's reasoning edge, and says
  nothing about the MAX tier.

## Addendum (2026-10-11): the two final checks

**Added after the first run, before this arm's data is collected.**

### H4 — a `num_ctx` change reloads the model

Pointing `MATH_MODEL` at the warm utility model only saves a load if compute's requested
context **matches** what utility uses. Ollama reloads a model when the requested context
changes, so `MATH_NUM_CTX=16384` against `UTILITY_NUM_CTX=4096` would trade an eviction
for a reload.

**Measure:** call `execute_python` on the utility model at 4096, again at 4096, then at
16384, again at 16384, then back to 4096 — recording each call's wall-clock and the
loaded context from `/api/ps`.

**Falsification:** if the loaded context does not change and the first call at each size
is **not** slower, there is no reload, and matching `MATH_NUM_CTX` does not matter.

### H5 — the dedicated model earns its keep on hard math

Business arithmetic ties. The open question is whether the dedicated 9B beats the utility
4B on **hard** problems (symbolic, multi-step, finance) — the case that would justify
keeping it.

**Measure:** the same harness on 6 hard problems (matrix determinant, a polynomial root,
a definite integral, an IRR, a binomial probability, a linear system), dedicated vs
utility, 2 trials each.

**Falsification:** if the utility model is correct on as many hard problems as the
dedicated one, the dedicated model has **no measured job** and can be dropped from the
fleet.

## Addendum (2026-10-11): the hard-math arm re-run, with the metric fixed

**Added after H5 was declared VOID, before this run's data.** The first hard-math arm
scored stdout by parsing numbers, and on hard problems it scraped unexplained values — a
4B "beating" a 9B was the tell that the metric was not measuring the answer. The harness
now **records the generated code** (`code`, `stdout_head`, `errored`), so a failure can be
told apart from a mis-parse.

This re-runs H5 unchanged — *does the dedicated model earn its keep on hard math?* —
against a metric that can answer it. It matters because the earlier retirement of the
dedicated model rested on a **tie** plus a **cost** (a load that evicted a warm model), and
that cost was then removed by raising `OLLAMA_MAX_LOADED_MODELS` to 4.

**Falsification (unchanged):** the utility model correct on as many hard problems as the
dedicated one → the dedicated model has no measured job.

**New, and the point of the re-run:** the failures are **classified** — a traceback or an
empty stdout is a **codegen** failure (the code ran but did not answer); a printed wrong
value is a **reasoning** failure. The distinction decides whether the fix is a prompt or a
model, and it is only visible now that the code is captured.
