# Verification — model_fleet_q4

## What was run

Three role probes against the local Ollama on 2026-10-08, all with the Q4
candidate family (the like-for-like quant):

| role | probe | harness |
|---|---|---|
| chat/tools | tool call + JSON + throughput | `model_swap_qwen35_claude` |
| utility | `extract_facts` on 5 turns | `model_fleet_q4` (this) |
| math | 8 executed-answer problems | `math_model_value` |

```
docker run --rm -v $(pwd):/app -w /app -e OLLAMA_URL=http://host.docker.internal:11434 \
  -e CANDIDATE_MODEL=sorc/qwen3.5-claude-4.6-opus-q4:9b \
  -e RESULT_PATH=/app/.../result_q4_9b.json \
  assistant python assistant/experiments/<harness>/experiment.py
```

Environment: host Ollama via `host.docker.internal`; `num_ctx=16384`,
`temperature=0`, `think=False`; `format="json"` for structured prompts.

## Non-mutation

Pure model I/O plus local subprocess execution. No brain is opened, so
`preflight.py` does not apply.

## Raw numbers

```
chat/tools   qwen3.5:9b          tools ✓  json ✓  ~40 tok/s
             q4:9b               tools ✓  json ✓  ~40 tok/s      (tie)

utility      qwen3.5:4b          7/7 expected values extracted
             q4:4b               4/7                          (candidate worse)

math         qwen3.8:27b         8/8, 6.8-50.5 s/problem
             q4:9b               8/8, 2.1-4.4 s/problem       (tie, ~4-10x faster)
             qwen3.5:9b          7/8
```

The Q4 9B printed `338350` for "sum of squares 1..100" — the problem the **Q8**
build (`sorc/...-q4` is a different repo from `sorc/...`) missed by omitting
`print`. So the Q8 build's miss was a quantization artifact, not a property of the
model.

## Threats and limits

- **Small probes:** one chat prompt, five extraction turns, eight math problems.
  They can reject; they cannot certify.
- **The math tie is on eight easy problems.** The 27B may win on harder math; the
  candidate's advantage here is speed and size, not demonstrated superiority.
- **Chat quality is unmeasured** — mechanics and throughput only.
- One run, one host.
