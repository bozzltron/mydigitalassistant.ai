# Experiment: is `sorc/qwen3.5-claude-4.6-opus` the right MATH_MODEL?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The `compute` tool runs a dedicated **math model**: given a math request, the model
writes Python, the backend executes it, and the stdout is the answer. The current
model is `qwen3.8:27b` (17 GB, on-demand).

The fleet-swap probe showed the candidate (`sorc/qwen3.5-claude-4.6-opus`, 10 GB)
is ~2.8× faster than the 27B and emits plausible code — but it checked only the
code's *shape*, not whether the **answer** is correct. For the math role, the
answer is the whole job. This measures it: run the code, compare to ground truth.

## Hypotheses

- **H1 (correctness — decisive).** On a fixed set of problems with known numeric
  answers, the candidate is correct at least as often as the 27B, measured by
  *executed* output vs ground truth.
- **H2 (latency).** The candidate is faster per problem than the 27B.
- **H3 (fit).** The candidate is smaller (10 GB vs 17 GB), so it fits more easily.

## Variables

- **Models:** the candidate; `qwen3.8:27b` (current math model); `qwen3.5:9b` (the
  chat fallback, for a floor).
- **Problems (fixed, ~8):** arithmetic, compound interest, NPV, a word problem,
  statistics (mean, population stdev), factorial, algebra, probability — each with
  a known numeric answer.
- **Measured per problem:** the executed answer vs ground truth, wall time, and
  whether a tool call was emitted.

## What counts as evidence

- **H1 is the decisive test.** If the candidate is correct on **fewer** problems
  than the 27B, keep the 27B. A tie or better, together with H2, makes the
  candidate the math model.
- Correctness is the executed stdout parsed for the final number, compared to
  ground truth within a small tolerance — not a substring signal.

## Falsification conditions

1. The candidate is correct on fewer problems than the 27B → keep `qwen3.8:27b`.
2. The candidate fails to emit a valid tool call on any problem → reject for math.
3. The candidate is slower than the 27B → no reason to swap.

## Method

`experiment.py` drives the **production path** — `OllamaClient.execute_python` with
`math_model` set per arm — so the model writes Python, the backend executes it, and
the stdout is parsed for the final number. Pure model I/O plus local execution; no
brain is opened, so `preflight.py` does not apply.

```
python assistant/experiments/math_model_value/experiment.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- **Eight problems is a probe, not a benchmark.** It can reject a bad math model
  cheaply; it cannot certify a good one.
- **The parse is "last number in stdout"** — fragile in general; the problems are
  chosen so the final print is the answer.
- One run, `temperature=0`; a single host.
