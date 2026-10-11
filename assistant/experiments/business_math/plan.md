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
