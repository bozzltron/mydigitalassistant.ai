# Verification — math_model_value

## What was run

`experiment.py` against the local Ollama on 2026-10-07, driving the **production
compute path** (`OllamaClient.execute_python` with `math_model` set per arm): the
model writes Python, the backend executes it in a subprocess, and the stdout is
graded against ground truth.

```
docker run --rm -v $(pwd):/app -w /app \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/math_model_value/experiment.py
```

Environment: host Ollama via `host.docker.internal`; `num_ctx=16384`,
`temperature=0`, `think=False`; `tool_choice="required"`. Eight problems with
known numeric answers.

## Non-mutation

Pure model I/O plus local subprocess execution. No brain is opened, no database is
touched, so `preflight.py` does not apply.

## Harness correction (before the recorded run)

The first run graded the **last number** in stdout. That was wrong: the models
print the answer *and* extra detail, so a correct answer was marked wrong —

```
qwen3.8:27b  compound interest:  "Total amount owed: $11,966.81\nTotal interest paid: $1,966.81"
             -> last number 1966.81  (graded FAIL; the answer 11,966.81 is right there)
qwen3.8:27b  NPV:  "NPV (sum of PVs): $3,992.71 … Year-by-year breakdown: …"
             -> last number from the breakdown  (graded FAIL; 3,992.71 is right there)
```

The grade is now "does the computation produce the right value anywhere in
stdout", within tolerance. Under it the 27B moves from 6/8 to 8/8.

## The candidate's one miss — investigated, real

The candidate failed "sum of the squares 1..100" with **empty stdout** on every
attempt. Reproduced through the production request path, the emitted tool call is:

```python
sum(i**2 for i in range(1, 101))     # computes the sum, prints nothing
```

No `print`, so stdout is empty and the answer is lost. The 9b wrote
`… = sum(...); print(…)`. This is a real, reproducible reliability gap, not a
harness artifact.

## Raw output

```
candidate                    7/8 correct  avg_wall=5.2s   max=9.5s
math baseline (qwen3.8:27b)  8/8 correct  avg_wall=13.5s  max=31.7s
chat fallback (qwen3.5:9b)   7/8 correct  avg_wall=3.4s   max=5.1s
```

## Threats and limits

- **Eight problems is a probe, not a benchmark.**
- **The grade is lenient** ("the value appears in stdout"), which is the price of
  the models printing extra detail. It is not a false-positive risk at these
  specific answers.
- One run, one host; `temperature=0` but model output is not perfectly
  deterministic.
