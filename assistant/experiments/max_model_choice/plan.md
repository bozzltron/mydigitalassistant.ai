# Experiment: is the opus Q4 9B a better MAX model than the 27B?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The max-intelligence tier is `qwen3.8:27b` — the escalation for genuinely hard
problems. It is also the slowest thing in the fleet (~1–3 min per tool round with
thinking on), and it is a **separate** on-demand load from the math tier, so a turn
that both escalates and computes loads two models.

`sorc/qwen3.5-claude-4.6-opus-q4:9b` (9.7B, Q4_K_M) currently backs the **math**
role. It matched the 27B on the math probe and is 4–10× faster — but that probe
used *eight easy* problems, and MAX exists for **hard** reasoning, so it says
nothing about this role.

**Is the opus Q4 9B at least as accurate as `qwen3.8:27b` on hard, multi-step
reasoning — and substantially faster?**

## Hypotheses

- **H1 (quality — the binding metric).** The candidate answers the hard problems
  within **1 of 10** of the incumbent. A MAX swap must not cost accuracy.
- **H2 (speed).** The candidate's median wall-clock per problem is **at least 2×
  faster** than the incumbent's.

## Variables

- **Models:** `qwen3.8:27b` (incumbent) and
  `sorc/qwen3.5-claude-4.6-opus-q4:9b` (candidate).
- **Config:** `think=True`, `temperature=0`, one model call per problem — the MAX
  tier's shape (reasoning, not the compute path).
- **Problems (fixed, committed with this plan):** 10 hard, multi-step problems
  with exact answers (combinatorics, careful multi-step arithmetic, counting,
  logic). Each prompt asks for a final line `ANSWER: <number>`, which is parsed —
  not "the last number", which the probe for math showed grades correct answers
  wrong when the model prints intermediate detail.
- **Measured per problem:** correctness, wall-clock seconds.

## What counts as evidence

- **Accuracy is the binding metric.** A faster model that is less accurate is a
  worse escalation tier: the whole point of MAX is the answer.
- Latency is the reason to consider the swap at all.

## Falsification conditions

1. Candidate accuracy **≥ 2 problems worse** than the incumbent → keep
   `qwen3.8:27b`.
2. Candidate accuracy **within 1** AND **≥ 2× faster** (median) → swap MAX to the
   candidate.
3. Accuracy within 1 but **not** ≥ 2× faster → keep the incumbent (no reason to
   churn).

## Method

Direct `OllamaClient.chat` with `think=True`; no brain, no tools, no network
beyond local Ollama.

```bash
python assistant/experiments/max_model_choice/experiment.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- **"Hard" is a judgement and the set is small (10).** It can separate a 9B from a
  27B if they differ, but it cannot certify a rate, and it is not a random sample
  of real MAX turns.
- **Reasoning only, not the tool loop.** MAX in production drives tools too; this
  probe isolates the reasoning capability, which is what the role is chosen for.
  A follow-up can run the tool loop if the accuracy is close.
- **One run per problem, temperature 0.** Ollama at temperature 0 is not perfectly
  deterministic; a close result is a tie, not a finding.
- The incumbent is a bigger model; on genuinely hard problems it may win, which is
  exactly what condition 1 is for.
