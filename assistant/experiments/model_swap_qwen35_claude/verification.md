# Verification — model_swap_qwen35_claude

## What was run

`experiment.py` against the local Ollama on 2026-10-07.

```
docker run --rm -v $(pwd):/app -w /app \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/model_swap_qwen35_claude/experiment.py
```

Environment: host Ollama via `host.docker.internal`; `num_ctx=16384`,
`temperature=0`; **`think=False`** and **`format="json"`** on the structured
prompt, both the production defaults. Models: `qwen3.5:9b` (chat), `qwen3.8:27b`
(math), `sorc/qwen3.5-claude-4.6-opus:latest` (candidate).

## Non-mutation

Pure Ollama I/O. No brain is opened, no database is touched, so `preflight.py`
does not apply.

## Harness corrections (before the recorded run)

The first run reported `json_ok=False` for the candidate. That was a harness
artifact, fixed before recording:

- **`think` was uncontrolled.** A thinking model leaked its `<analysis>` preamble
  into `content`; production sends `think=False`. Now set.
- **The structured prompt did not use JSON mode.** Production's extractor uses
  `format="json"`; now set.
- **`json_ok` required a JSON *array*.** Under `format="json"` both models return
  an *object* (`{"benefits": [...]}`); the check now accepts valid JSON of any
  shape.

## Raw output

```
[chat baseline] qwen3.5:9b        qa 1.6s  json 2.3s(ok)  tool 1.5s(call)   ~40 tok/s
[math baseline] qwen3.8:27b       code 11.5s(load 8.2s)                     ~9 tok/s
[candidate]       sorc/qwen3.5-…  qa 6.8s(load 4.1s) json 5.0s(ok)
                                  tool 1.8s(call) code 1.3s                 ~27 tok/s
```

Both code answers are correct (`sum(i**2 …)` / `sum(i*i …)` over 1..100 →
338,350).

## Threats and limits

- **Four prompts.** This kills a hypothesis; it cannot certify a model. The
  `code_signal` and `json_ok` checks are crude.
- **Quality is not measured.** The probe checks mechanics (does it call a tool,
  does it emit parseable JSON, does it emit code) — not whether the answers are
  *better or worse* than the baselines. That is the decisive missing measurement.
- **Latency is a warm measurement** except where a `load_s` is shown; the
  candidate's first call paid a 4.1s cold load.
- One run, one host (M4 Pro, 48 GB).
