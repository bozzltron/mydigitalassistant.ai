# Result — which models make a good extraction council?

**A council works — and the answer to "is it a model we don't have?" is no.** The
incumbent misses ~1 fact in 9; a two-model council closes that to **zero** on this set.
But the diversity comes from **`sorc/qwen3.5-claude-4.6-opus-q4:4b` — a Qwen-based
fine-tune already on disk** — not from the cross-lineage models. Pulling `llama3.2:3b`
and `gemma3:4b` bought **nothing** the fine-tune did not already provide.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — the incumbent misses ≥ 1 in 10** | **holds** | 2/18 = 11% |
| **H2 — the union beats the best single by ≥ 5 pts** | **holds** | 1.00 vs 0.89 |
| **H3 — cross-lineage overlap < same-lineage** | **fails** | cross 0.20 > same 0.15 |
| **H4 — disagreement tolerable** | **unresolved** | 0.28 raw (an upper bound) |

## Falsification conditions, resolved

1. H1 fails → not triggered; there *is* a gap.
2. H2 fails → not triggered; the union is materially higher.
3. H3 fails → **triggered**: cross-lineage did not add diversity → **do not pull a new
   model for diversity's sake.**
4. H4 → not resolvable from raw value-sets; see below.

## The finding

- **The council is a same-lineage pair, and it is already on disk.** `qwen3.5:4b` +
  `sorc/qwen3.5-claude-4.6-opus-q4:4b` reaches 1.00 where each alone reaches 0.89. The
  fine-tune adds exactly the incumbent's two misses.
- **Lineage was not the lever.** The two *pulled* models: `llama3.2:3b` adds the same two
  turns as the fine-tune but takes ~2× the latency (7.8 s vs 4.1 s median); `gemma3:4b`
  and `qwen2.5:7b` are **worse extractors** (0.56 / 0.50) whose unique catches are empty.
  So their low overlap is incompetence, not healthy disagreement — which is precisely the
  confound the plan named, and it is the one that fired.
- **The fleet is a monoculture, but the monoculture is not the problem here.** Common-mode
  failure was real for the *first* miss and irrelevant for the *second* — and a
  same-lineage sibling fixed both. Diversity was available **without leaving the family**.
- **The council's ceiling is the warm set.** `qwen3.5:4b + qwen3.5:9b` — the two models
  already resident — reaches **0.94** for **zero extra residency**. That is the number to
  weigh against the best pair's 1.00, which needs a third model loaded.

## What this proves, and what it does not

- **Proved:** a council raises extraction recall on this set, and the member that does the
  work is a same-lineage model we already have.
- **Not proved:** that lineage never helps — only that *these two* cross-lineage models did
  not. A competent cross-lineage extractor was not tested.
- **Not proved:** that the gap is worth closing. One of the two misses (`7`, the trip
  month) is missed by **every competent model**, so the effective gap is arguably **1 fact
  in 18** — and it costs a second model load to catch it.

## Recommendation

**Do not pull a model for the council.** If a council is built, draw it from models
already resident (`qwen3.5:4b` + `qwen3.5:9b`, union 0.94, no extra residency) or from the
already-downloaded fine-tune (union 1.00, one extra load). The open question is not *which
models* but *whether one fact in nine is worth a second model on the learning path* — and
the residency cost is now measured rather than assumed.

## Addendum: performance — parallel is fine, residency is the constraint

| | latency | extra residency |
|---|---|---|
| incumbent alone (`qwen3.5:4b`) | 5.1 s | — |
| best pair, **serial** | 11.3 s (sum) | +1 model (evicts a warm one) |
| best pair, **parallel** | **7.2 s** | +1 model (evicts a warm one) |
| warm pair, parallel (`4b`+`9b`) | 12.3 s | **none** |

**Parallelism works and is worth ~36%.** The pair costs 7.2 s concurrently against 11.3 s
serially — the slower member plus 18%, not the sum. Ollama does run two different models at
once, so a council should always be run in parallel; a serial council is the expensive
version of the same thing.

**The binding constraint is `OLLAMA_MAX_LOADED_MODELS`, not RAM.** The host has **48 GB**
and would happily hold a fourth model, but the default cap is **3** and the warm set
already fills it. Measured: running `sorc/q4:4b` alone **evicted `qwen3.5:4b`** — a warm
model. So a council member outside the warm set does not merely add load, it **displaces
the chat/utility models** and pays their reload on the next turn. That is the real cost,
and it is invisible in the accuracy numbers.

**Two coherent options, both cheap in different currencies:**
- **Warm pair** (`qwen3.5:4b` + `qwen3.5:9b`) — union **0.94**, **zero** extra residency,
  but **12.3 s** (the 9b dominates).
- **Best pair** (`qwen3.5:4b` + `sorc/q4:4b`) — union **1.00**, **7.2 s**, but it evicts a
  warm model unless the cap is raised (48 GB affords it).

**So: yes, a local Ollama handles a council — in parallel, within the loaded-model budget.**
The decision is now about *eviction policy* (draw from the warm set, or raise the cap) and
*whether one fact in nine justifies either* — not about raw capacity.
