# Experiment: Memory Health Check

## Status

**A tool, not a one-shot experiment.** `__main__.py` is a reusable diagnostic
that prints a JSON snapshot of memory vitals on demand; it does not test a
hypothesis, so there is no `result.md` to write. This document is its reference.

## Question
Is the memory system healthy? Quick snapshot of key vitals.

## Metrics (single SQL query each)

| Metric | Healthy Range |
|--------|---------------|
| Conflict rate | < 5% |
| High-confidence slots (>0.7) | > 30% |
| Reinforced slots (EXPAND in history) | > 20% |
| Frames with associations | > 60% |
| Essential frames deleted | 0 |
| Pending conflicts | 0 |

## Run

```bash
# From repo root
docker run -it --rm -v $(pwd):/app -w /app assistant python -m assistant.experiments.memory_health /path/to/assistant.db
```

## Output

JSON to stdout + `memory_health_<timestamp>.json` in cwd. Exit code 1 if any metric outside healthy range.