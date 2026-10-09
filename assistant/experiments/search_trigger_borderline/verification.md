# Verification — search_trigger_borderline

## What was run

`experiment.py` against the local Ollama on 2026-10-09, driving the decision path
(router → reasoner → storage veto) with the utility model. No generation, no brain
writes.

```
docker run --rm -v $(pwd):/app -w /app -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/search_trigger_borderline/experiment.py
```

Environment: `qwen3.5:4b` utility model; the reasoner ran on an **empty** memory
context (sufficiency `NONE`).

## Non-mutation

Pure model I/O. No brain is opened, so `preflight.py` does not apply.

## Raw output

```
borderline recall:       30% (3/10)
general-knowledge spec:  100% (8/8)
```

Searched (3): "Summarize the latest research on sleep and memory",
"What's the current state of quantum computing?", "Compare the top project
management tools."

Missed (7): "best electric guitar for a beginner", "recent research on lithium-ion
battery degradation", "current best practices for password managers", "which laptop
should I buy for video editing", "are standing desks worth it", "a good budget air
fryer", "is intermittent fasting still recommended".

## What the pattern shows

The router searches when the query **names a freshness cue** — "latest", "current
state", "compare" — and stays silent on the classes that need current sources most:

- **recommendation** ("best X for a beginner", "which Y should I buy", "a good Z",
  "are X worth it", "is X still recommended") → always no search;
- **"recent research on …"** → no search (but "latest research on …" → search).

So the trigger keys on a vocabulary of freshness words rather than on *whether a
current source would improve the answer*. That is the gap.

## Threats and limits

- Empty memory context isolates the router/veto (as in `search_trigger`).
- "Should search" is a judgment for this class; the bar was set at 80% with a 50%
  falsification floor precisely because some are arguable. 30% is far below both.
- The router is a model; `temperature=0` but not perfectly deterministic.
- 18 queries: confirms a systematic gap, does not certify a rate.
