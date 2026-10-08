# Verification — context_budget_value

## What was run

`experiment.py` at commit `0bcf0e9` (pre-registered before any data), run once
against the local Ollama on 2026-10-07.

```
docker run --rm -v $(pwd):/app -w /app \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/context_budget_value/experiment.py
```

Environment: the `assistant` image; host Ollama reached via
`host.docker.internal`; model `qwen3.5:9b`; `CHAT_NUM_CTX=16384`. The harness
built the real tool-loop prompt with the real builtin tools (search disabled, so
18 tools), installed the budget from the measured components, and drove
`stream_tool_loop` with `max_turns = settings.max_tool_rounds` (6).

## Non-mutation

The harness opens a **scratch** database in a `TemporaryDirectory` and seeds the
sandbox under `filesystem.SANDBOX_ROOT`, deleting every created file in a
`finally`. The live brain is never opened and its volume is never mounted, so
`preflight.py` (which gates experiments that open a brain copy) does not apply.

## Raw output

```
[read_big_csv]      allowance=28260 chars  peak=8194/16384 tok  truncated=False  tool_result_chars=29452  dropped=0
[compare_two_csvs]  allowance=28228 chars  peak=8194/16384 tok  truncated=False  tool_result_chars=29586  dropped=1
[long_history]      allowance=10288 chars  peak=7026/16384 tok  truncated=False  tool_result_chars=27     dropped=0
```

(`list_files` was dropped from the scenario set after the first run: the seeded
files had no memory frames, and `list_files` skips orphaned files when a user_id
is given, so it returned nothing and could not exercise the listing cap. See
*Threats*.)

## Verification of the numbers

- `allowance_chars = (16384 − ceil(fixed_cost_chars/4) − 4096) × 4`. For
  `read_big_csv`: fixed 20,891 → 5,223 tok → 7,065 tok allowance → 28,260 chars.
  Matches the row exactly, so the budget math is behaving as specified.
- `long_history` shrinks the allowance to 10,288 chars because 6 × 3,000-char
  history turns enter the fixed cost — the adaptivity the budget exists for.
- The measured peak (8,194 tok) is **below** the component sum (≈48k chars) at the
  naive ÷4 ratio; at ~5.9 chars/token the repetitive CSV content explains it
  (repeated `userN@example.com,Name N,TX` tokenizes well). So `prompt_eval_count`
  is the full prompt, and the ÷4 budget is *conservative* for this content.

## Threats and limits

- **Four-then-three scripted turns, one model, a synthetic corpus.** This can kill
  a hypothesis; it cannot certify a design.
- **The aggregate scenario needed a two-file comparison.** A single-read turn
  (`read_big_csv`) is `dropped=0`; only a turn that genuinely needs two large
  results (`compare_two_csvs`) fires the aggregate. So the aggregate's *value* is
  conditional on how often users ask multi-file questions — the mechanism is
  exercised, its frequency is not measured here.
- **`list_files` was not exercised** (seeded files had no frames). The listing cap
  is a per-result (T2) cap, covered by `test_context_budget.py`; it is out of
  scope for this experiment's aggregate question.
- **`chars_per_token = 4`** remains an estimate; the direction of error depends on
  the content (conservative for repetitive CSV, possibly optimistic for JSON).
- **The round numbers (6/12) were not measured** — this experiment fixed
  `max_turns` at the base; it says nothing about whether 6 is the right ceiling.
