# Experiment: can `sorc/qwen3.5-claude-4.6-opus` replace the chat (and math) models?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The fleet's **chat** and **tools** roles run `qwen3.5:9b` (6.6GB), and the
on-demand **math** role runs `qwen3.8:27b` (17GB). A locally-pulled candidate,
`sorc/qwen3.5-claude-4.6-opus:latest` (9.7B, Q8_0, 10GB; capabilities
`completion / tools / thinking / vision`), might do both jobs — and possibly more
(tools, max) — with one model.

This is a fleet decision with a real cost (a larger warm set) and a real risk (a
swap changes *every* turn). Per AGENTS.md it gets measured before it is made.

## Hypotheses

- **H1 (tools — the gate).** The candidate emits a valid tool call as reliably as
  the baseline on a representative tool prompt. If it does not, it cannot take the
  chat role, because chat and tools share one model.
- **H2 (latency).** A *warm* turn's latency is comparable to the baseline and
  inside the hot-path budget (~2s of perceivable wait). A candidate much slower
  belongs in an on-demand tier, not the warm set.
- **H3 (math/codegen).** On the same small arithmetic/code problems, the candidate
  produces usable Python for the `compute` tool at least as often as the 27B
  baseline. If it is worse, the 27B stays the math model.
- **H4 (fit).** The candidate's weights plus the rest of the warm set stay within
  the memory budget (M4 Pro, 48GB; the warm set must stay resident).

## Variables

- **Models.** Baseline `qwen3.5:9b` (chat/tools) and `qwen3.8:27b` (math);
  candidate `sorc/qwen3.5-claude-4.6-opus:latest`.
- **Prompts** (fixed and small, the same for every arm): one general QA, one
  structured-output (JSON), one tool-call probe, one codegen/math prompt.
- **Measured per prompt:** wall time, load time, prompt-eval time, eval time,
  tokens/sec, and for the tool probe whether `tool_calls` came back.

## What counts as evidence

- **H1 is the primary test.** A single absent/malformed tool call on the probe is
  a fail for the chat/tools role, and will be reported as such.
- H2 compares **warm** latency; the first prompt's cold-load number is recorded
  separately and is not the comparison.
- H3 is decided by code usability on the fixed problems, not by verbosity.

## Falsification conditions

1. The tool probe returns no valid call → reject for chat/tools (keep
   `qwen3.5:9b`).
2. Math/codegen is worse than the 27B → keep `qwen3.8:27b` for math.
3. Warm latency is above the budget → the candidate goes to an on-demand tier only.
4. The warm set no longer fits → reject outright.

## Method

Run `experiment.py` against the local Ollama. It is **pure model I/O** — it does
not open the brain, so `preflight.py` (which gates experiments that open a brain)
does not apply.

```
python assistant/experiments/model_swap_qwen35_claude/experiment.py
```

It writes `result.json`. `result.md` is written only after `verification.md`.

## Threats and limits

- Four prompts is a spot-check, not a benchmark. It can **reject** a bad candidate
  cheaply; it cannot certify a good one — production observation still decides.
- Ollama's `eval_duration` excludes load; compare warm numbers only.
- The candidate's name is branding, not a provenance claim. It is a local GGUF
  (which is what the privacy rule requires), but its training lineage is not
  verified here.
