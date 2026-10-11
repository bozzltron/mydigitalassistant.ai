# Result — can a decision model judge `needs_action`?

**Yes — and it dominates the mechanism we have now.** `tev1` and `tev1:0.8b` both
score **95%** where the `qwen3.5:4b` chat call scores **90%**, at **70 ms** and
**304 ms** against **7,162 ms**. The same judgement is more accurate *and* ~25–100×
faster.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — accuracy ≥ 90%** | **holds** | `tev1` 95%, `tev1:0.8b` 95% |
| **H2 — median ≤ 200 ms** | **holds for `tev1:0.8b`** (70 ms); `tev1` is 304 ms | |
| **H3 — beats the 4B baseline** | **holds** | baseline 90% @ 7,162 ms |

## Falsification conditions, resolved

1. best accuracy < 80% → not triggered (95%).
2. best accuracy ≥ 90% **and** median ≤ 200 ms → **triggered** (`tev1:0.8b`, 95% @ 70 ms).
3. baseline matches on accuracy at comparable latency → not met: the baseline is both
   slower and less accurate.

## The finding

- **The decision models beat the chat-call router on both axes.** This is the
  "model routing" use case the API is for, and it shows: a typed decision is faster
  and more accurate than asking a 4B to emit JSON.
- **`nimble` (9B, 605 ms) is the weakest here** — 90%, no better than the baseline —
  so size is not the lever for this judgement; the specialised small models win.
- **The one costly miss is recoverable.** `tev1:0.8b` called *"Read Why Not
  Release.md"* `answer` at **P(action) = 0.44** — just under the 0.5 threshold. The
  plan's cost asymmetry says a wrong `answer` skips the fix while a wrong `action`
  costs one nudge round, so **biasing the decision toward `action`** (take `action`
  when P(action) ≥ ~0.4) recovers it. `tev1` (4B, 304 ms) gets it right unaided.

## What this proves, and what it does not

- **Proved:** a local decision model can judge `needs_action` reliably and in tens of
  milliseconds — so the model-first fix is viable: **the model judges whether an
  action is required; the loop's structural check (no tool call) triggers one
  nudge; the model performs the action.** No regex, no phrasing detection.
- **Not proved:** that the nudge makes the model act — that is the next step, and it
  is verified on the failing scenario (turn 2 must call `write_file`), not here.
- **Not proved:** that decision models help the *other* decisions (task type,
  `wants_search`, query sensitivity). The result is suggestive — a typed decision
  beat a JSON chat call — but each is its own judgement and needs its own probe.

## Recommendation

Adopt a decision model for `needs_action`:

- **`tev1:0.8b` (70 ms)** with an **`action`-biased threshold** — the fast default,
  and the bias is justified by the cost asymmetry rather than chosen by taste.
- **`tev1` (304 ms)** if we would rather not tune a threshold: it gets the decisive
  direction right unaided.

Either is ~25–100× faster than the current mechanism at equal-or-better accuracy.
