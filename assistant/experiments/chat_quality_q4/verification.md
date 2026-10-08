# Verification — chat_quality_q4

## What was run

`experiment.py` against the local Ollama on 2026-10-08: 10 prompts, both models
answered (`think=False`, `temperature=0`), then `qwen3.8:27b` judged each pair
**blind** (answers labelled A/B, model names hidden) in **both orders**.

```
docker run --rm -v $(pwd):/app -w /app -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/chat_quality_q4/experiment.py
```

## Non-mutation

Pure Ollama I/O. No brain is opened, so `preflight.py` does not apply.

## Raw tally

```
candidate 2   incumbent 0   tie 5   flip 3
position-robust win rate: 100% of decided pairs
```

Per prompt (order1 / order2):

```
confidence interval     flip   (TIE / B)
household energy        candidate (A / B)
Roth vs traditional IRA flip   (A / A)     <- position-A bias
recipe scaling          tie    (TIE / TIE)
summarize the park      tie    (TIE / TIE)
three benefits          tie    (TIE / TIE)
Chicago vs LA time      tie    (TIE / TIE)
why the sky is blue     candidate (A / B)
rescue dog              flip   (TIE / A)
haiku                   tie    (TIE / TIE)
```

## Integrity notes

- **The judge is noisy.** 3 of 10 pairs changed verdict with position — one is a
  clean position-A bias (`A / A`), two are inconsistent (`TIE / B`, `TIE / A`).
  This is exactly the failure mode the position swap exists to expose, and it
  means the *decided* pairs (2 of 10) are a thin basis.
- **No incumbent wins anywhere.** The candidate is never judged worse; it is
  mostly judged equal.
- **The sample is 10 prompts.**

## Threats and limits

- **LLM-judge bias** (position, verbosity, family) is not eliminated; the swap
  exposes it rather than removing it.
- A single judge, one run, one host.
- Ten prompts can reject a claim; they cannot certify one.
