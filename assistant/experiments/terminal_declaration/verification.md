# Verification — terminal_declaration

## What was run

`experiment.py` on 2026-10-11: a deterministic arm, then 11 requests × 2 policies
interleaved (22 live turns) against local Ollama (`qwen3.5:9b`), real tools, scratch
store and scratch sandbox.

```
docker run --rm -v $(pwd):/app -w /app -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/terminal_declaration/experiment.py
```

## Non-mutation

Scratch store and sandbox only (`tempfile.mkdtemp`, removed at the end); the guard
aborts if pointed inside `/app/data`. No brain. No network beyond localhost.

## Raw output

**Deterministic (H4):**

```
[D terminal] rounds=2 finalized=True acting=1 outstanding_consulted=0
```

A prose-ending turn is re-prompted **structurally**, and the completion judgement is
never consulted. **H4 holds.**

**Live (per class):**

| class | metric | C (judged) | F (terminal) |
|---|---|---|---|
| answer | finalized | 0/5 | **4/4** |
| answer | rounds (median) | 1.0 (1) | 1.2 (1) |
| change | acted | 4/4 | **4/4** |
| change | rounds (median) | 2.8 (2) | 3.8 (4) |
| announce | acted | 2/2 | **2/2** |
| announce | rounds (median) | 2.0 (2) | 3.5 (4) |
| all | rounds (median) | 1.82 (2) | 2.70 (2.5) |

One F turn errored (`500 Internal Server Error` from Ollama) and is excluded; it is
the reason `answer F` is 4/4 rather than 5/5.

## The shape of the cost

Compliance and completion are **equal**, but the rounds are not:

- **Answer turns cost nothing extra** (median 1 both) — the `finalize` call rides in
  the round the answer would have used.
- **Action turns cost ~1–2 rounds** (median 2 → 4). The F contract tells the model to
  "make the file, then call `finalize`", and the traces show it doing a verification
  pass first: `write_file, read_file, finalize` and, once, `write_file ×4` before
  declaring. C's turns are `write_file` then prose.

So the extra cost is not the terminal call itself; it is that **naming an explicit end
state makes the model check its work before declaring it**. That is a real behavioural
difference, not an accounting artifact.

## Threats and limits (what remains after the run)

- **n = 4–5 per class per policy.** One run per request; the round counts are noisy
  (C's change rounds were 2, 2, 3, 4). The medians separate more clearly than the means.
- **The system prompt carried no memory context** — the harness has no brain — so the
  contract was measured against a thinner prompt than production carries.
- **One infrastructure error** (Ollama 500) removed an F turn; it is not a policy result.
- **The structural check's known hole** — it accepts a `finalize` that skipped the
  action — was not hit: `change acted` and `announce acted` were 4/4 and 2/2. But n is
  small, so the hole is unmeasured rather than proven absent.
- The deterministic arm proves the loop's policy, not the world.

## Second run (addendum): the tightened contract

`experiment.py` re-run on 2026-10-11 with three policies interleaved (C, F, F2) on the
same 11 requests. C and F reproduced the first run (mean rounds 2.00 / 2.60 vs 1.82 /
2.70), so the comparison is stable.

| class | metric | C | F | F2 (tight) |
|---|---|---|---|---|
| answer | finalized | 0/5 | 4/4 | **5/5** |
| answer | rounds (median) | 1 | 1 | **2** |
| change | acted | 4/4 | 4/4 | **4/4** |
| change | rounds (median) | 2.5 | 4.0 | **5.0** |
| announce | acted | 2/2 | 2/2 | **2/2** |
| announce | rounds (median) | 2.0 | 2.5 | **3.0** |
| all | rounds (mean / median) | 2.00 / 2 | 2.60 / 2.5 | **3.18 / 2** |

One F answer turn errored (`500`) and is excluded.

**The tightening made it worse, not better.** Compliance and completion held (5/5,
4/4, 2/2), but action-turn rounds *rose* (change median 4.0 → 5.0; `write_file ×4/×5`
still appears), and answer turns stopped being free (median 1 → 2). Forbidding the
re-check did not remove the work; the model did more of it. So the extra cost is
**inherent to the explicit end state**, not to the verification pass.
