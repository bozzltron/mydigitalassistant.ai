# Experiment: which model best fills each role (like-for-like Q4)?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The `sorc/qwen3.5-claude-4.6-opus-q4` family ships real Q4 builds whose sizes match
the incumbents exactly (`:9b` = 6.59 GB like `qwen3.5:9b`; `:4b` = 3.39 GB like
`qwen3.5:4b`). That removes the quantization confound that made the earlier
chat-latency comparison unfair, and lets each role be compared like-for-like.

**Which model is best for each role — chat/tools, utility, math — and does any
candidate beat the incumbent on the role's binding metric?**

## Hypotheses

- **H1 (chat/tools).** At equal quant, the candidate `:9b` matches `qwen3.5:9b` on
  the tool-call gate and on throughput. If so, chat is a *quality* decision, not a
  mechanical one.
- **H2 (utility).** The candidate `:4b` extracts the same facts as `qwen3.5:4b`
  from representative turns, at comparable throughput.
- **H3 (math).** The candidate `:9b` solves the executed-answer problems at least
  as often as `qwen3.8:27b`.

## Variables

- **Models:** candidate `:9b` / `:4b`; incumbents `qwen3.5:9b` (chat/tools),
  `qwen3.5:4b` (utility), `qwen3.8:27b` (math).
- **Probes (one per role):**
  - **chat/tools** — a tool-call probe (does it emit a valid call) + throughput.
  - **utility** — `extract_facts` on representative turns; the fraction of expected
    values that appear in the extracted slots.
  - **math** — the `math_model_value` problems, executed and graded.
- **Measured:** the role's score, plus throughput.

## What counts as evidence

- **A swap requires beating the incumbent on the role's binding metric.** For chat
  that is quality (mechanics and latency must be no worse); for utility, extraction
  coverage; for math, executed correctness.
- A tie keeps the incumbent: there is no reason to churn a working model.

## Falsification conditions

1. Candidate fails the chat tool gate, or is slower than `qwen3.5:9b` at equal
   quant → keep `qwen3.5:9b`.
2. Candidate extracts fewer facts than `qwen3.5:4b` → keep `qwen3.5:4b`.
3. Candidate solves fewer math problems than `qwen3.8:27b` → keep `qwen3.8:27b`.

## Method

`experiment.py` runs the **utility** extraction probe (the role without a harness
yet); chat/tools is the `model_swap_qwen35_claude` probe and math is the
`math_model_value` probe, both re-run with the Q4 candidate. Pure model I/O plus
local subprocess execution; no brain. The fleet decision is recorded in
`docs/MODEL_SELECTION.md`.

```
python assistant/experiments/model_fleet_q4/experiment.py
```

## Threats and limits

- Small probes (one chat prompt, five extraction turns, eight math problems): they
  can reject, not certify.
- Extraction frame/key names are model-chosen; the probe checks the expected
  *values* appear, not that they land on a specific key.
- One run, one host.
