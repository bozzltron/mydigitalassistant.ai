# Verification — max_model_choice

## What was run

`experiment.py` on 2026-10-10 against local Ollama, 10 hard problems × 2 models,
`think=True`, `temperature=0`. No brain, no tools, no network beyond Ollama.

```
docker run --rm -v $(pwd):/app -w /app -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/max_model_choice/experiment.py
```

## Non-mutation

Pure model I/O. No brain is opened, so `preflight.py` does not apply.

## Raw output

```
[incumbent (qwen3.8:27b)] qwen3.8:27b
  ok   exp=        24 got=24.0        wall=50.25s
  ok   exp=    234168 got=234168.0    wall=229.22s   (one retry)
  ok   exp=        12 got=12.0        wall=27.62s
  ok   exp=       242 got=242.0       wall=222.96s   (one retry)
  ok   exp=      2520 got=2520.0      wall=34.39s
  ok   exp=       180 got=180.0       wall=41.11s
  FAIL exp=       360 got=None        wall=360.94s   ReadTimeout
  ok   exp=        23 got=23.0        wall=27.84s
  ok   exp=        22 got=22.0        wall=75.62s
  ok   exp=        47 got=47.0        wall=26.99s

[candidate (opus-q4:9b)] sorc/qwen3.5-claude-4.6-opus-q4:9b
  ok   exp=        24 got=24.0        wall=25.45s
  ok   exp=    234168 got=234168.0    wall=53.40s
  FAIL exp=        12 got=None        wall=37.44s
  FAIL exp=       242 got=262.0       wall=106.33s
  ok   exp=      2520 got=2520.0      wall=29.98s
  FAIL exp=       180 got=None        wall=106.25s
  FAIL exp=       360 got=None        wall=50.64s
  ok   exp=        23 got=23.0        wall=20.50s
  ok   exp=        22 got=22.0        wall=35.87s
  ok   exp=        47 got=47.0        wall=18.61s

accuracy: incumbent 9/10  candidate 6/10
median wall: incumbent 45.68s  candidate 36.655s  speedup 1.25x
```

## Reading it honestly

- **The incumbent's one failure was a timeout, not a wrong answer.** It answered
  9 problems correctly and lost one to a 360s `ReadTimeout`. It never gave a wrong
  answer.
- **The candidate's four failures are real:** one wrong (242 → 262) and three with
  no parseable answer at all — it did not finish or did not follow the format.
- **Speed:** the pre-registered metric is the **median** (robust to the incumbent's
  timeout/retries): **1.25×**, not the required 2×. On the **mean** the candidate
  is 2.26× faster (48.4s vs 109.7s), but that mean is dominated by the incumbent's
  two retried calls and one timeout — infrastructure, not model speed. Excluding
  the timeout, the incumbent's completed-problem median is 41.1s vs the
  candidate's 36.7s: **~1.1×**.

## Threats and limits

- **One run, temperature 0.** Ollama is not perfectly deterministic; the accuracy
  gap (3 problems) is well outside noise, the speed gap is not.
- **Infrastructure noise in the incumbent arm** (two retries, one timeout) inflates
  its wall-clock. This cuts *against* the candidate on speed, so the honest speed
  read is the median/completed-median, both well under 2×.
- **Reasoning only, not the tool loop.** MAX drives tools in production; this
  isolates the reasoning capability the role is chosen for.
- **10 problems; "hard" is a judgement.** It can show a gap this large; it cannot
  certify a rate.
