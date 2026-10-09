# Verification — search_trigger

## What was run

`experiment.py` against the local Ollama on 2026-10-09, driving the decision path
(router → reasoner → storage veto) with the utility model. No generation, no brain
writes.

```
docker run --rm -v $(pwd):/app -w /app -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/search_trigger/experiment.py
```

Environment: `qwen3.5:4b` utility model; the reasoner ran on an **empty** memory
context (sufficiency `NONE`), which isolates the trigger.

## Non-mutation

Pure model I/O. No brain is opened, so `preflight.py` does not apply.

## Raw output

```
must-search recall:      100% (8/8)
must-not specificity:    100% (7/7)
```

Every must-search query set `wants_search=True` with a distilled query (e.g.
"Nintendo Switch 2 current price", "weather in Chicago tomorrow", "2026 ACL
submission deadlines"); every must-not-search query set `wants_search=False`.

## Follow-up probe: the live queries that did *not* search

Three live search-worthy turns had not searched, so I ran those queries (and a
near-miss) through the same path:

```
Explain recent research on lithium-ion battery degradation.  wants=False -> no search
What are the best acoustic guitar strings for beginners?     wants=False -> no search
Explain how photosynthesis works.                            wants=False -> no search
Summarize the latest research on sleep and memory.           wants=True  -> search
```

So the discrepancy is **not** a systematic suppression — it is the router's
discretion on **borderline** queries. Clear external queries ("latest", prices,
weather, deadlines) all trigger; "explain / best X for beginners / recent
research" are sometimes judged general knowledge and vetoed.

## Threats and limits

- **Empty memory context** biases the reasoner toward search; production memory
  could change sufficiency. This isolates the router/veto, not the full retrieval
  outcome.
- The router is a model; `temperature=0` but not perfectly deterministic.
- 15 pre-registered queries plus 4 follow-up probes: it can expose a suppression,
  not certify a rate.
