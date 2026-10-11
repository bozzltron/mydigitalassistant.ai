# Verification — decision_model_routing

## What was run

`experiment.py` on 2026-10-10 against local Ollama 0.35 (`/v1/systemone` for the
decision models, `/api/chat` for the 4B baseline). 20 labelled requests × 4 arms.
No brain, no tools.

```
docker run --rm -v $(pwd):/app -w /app -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/decision_model_routing/experiment.py
```

## Non-mutation

Pure model I/O. No brain is opened, so `preflight.py` does not apply.

## Raw output (summary)

```
nimble         accuracy  90.0% (18/20)  median  605.0ms
tev1           accuracy  95.0% (19/20)  median  303.5ms
tev1:0.8b      accuracy  95.0% (19/20)  median   70.0ms
qwen3.5:4b     accuracy  90.0% (18/20)  median 7161.5ms
```

## The failures, and which direction they fall

| Model | Its one/two misses |
|---|---|
| `tev1:0.8b` | **`Read Why Not Release.md` → `answer`** — a false negative on an unambiguous action |
| `tev1` | `How many books are on my reading list?` → `action` (a label that is arguably right) |
| `nimble` | that one, **plus** `What did my morning briefing find yesterday?` → `action` |
| `qwen3.5:4b` | those same two |

The direction matters: the plan says a wrong **`answer`** skips the fix entirely,
while a wrong **`action`** costs one nudge round. Only `tev1:0.8b`'s miss is the
costly direction.

## The probability probe that explains it

Re-asking the two contested requests directly:

```
tev1:0.8b  Read Why Not Release.md        choice=answer  P(action)=0.441  conf=0.010
tev1       Read Why Not Release.md        choice=action  P(action)=0.696  conf=0.114
nimble     Read Why Not Release.md        choice=action  P(action)=0.724  conf=0.150

tev1:0.8b  How many books are on my list? choice=answer  P(action)=0.251  conf=0.188
tev1       How many books are on my list? choice=action  P(action)=0.689  conf=0.105
nimble     How many books are on my list? choice=action  P(action)=0.834  conf=0.352
```

`tev1:0.8b`'s miss sits at **P(action) = 0.44** — just under a 0.5 threshold. Since
the costs are asymmetric, **biasing the decision toward `action`** (take `action`
when P(action) ≥ ~0.4) recovers it without a new model.

## Threats and limits

- **Two `answer` labels are arguable.** "How many books are on my reading list?" and
  "What did my morning briefing find yesterday?" may legitimately require a file
  read — so the decision models calling them `action` may be **right** and the label
  wrong. That would make `tev1` effectively 100% and lower `nimble`/baseline.
- **Confidences are low** (0.01–0.35), matching the blog's own caveat. A `choice` is
  a decision, not a certainty; a near-tie can flip on a rerun.
- **20 requests is a probe**, not a rate.
- The model judges the **request**, not whether the turn *completed* it — completion
  is the loop's structural check (no tool call), not this model's job.
