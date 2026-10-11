# Verification — extraction_council

## What was run

`experiment.py` on 2026-10-11: 6 candidate models × 18 labelled turns through the
production extraction path (`extractor.extract_facts`, the candidate as `utility_model`)
against local Ollama. Sequential (one model at a time).

```
docker run --rm -v $(pwd):/app -w /app -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/extraction_council/experiment.py
```

## Non-mutation

No brain is opened; extraction is called directly. No tools. No network beyond localhost.

## Raw output

```
model                                 lineage  recall  schema  missed
qwen3.5:4b                            qwen      0.89    0.33  [7, 14]
qwen3.5:9b                            qwen      0.89    0.39  [12, 14]
qwen2.5:7b                            qwen      0.50    0.28  [3,7,8,9,10,11,12,14,16]
sorc/qwen3.5-claude-4.6-opus-q4:4b    qwen      0.89    0.33  [5, 10]
llama3.2:3b                           llama     0.89    0.28  [5, 16]
gemma3:4b                             gemma     0.56    0.28  [5,7,8,9,10,14,16,17]

best single   qwen3.5:4b  recall 0.89
best pair     qwen3.5:4b + sorc/qwen3.5-claude-4.6-opus-q4:4b  union 1.00
mean miss-overlap   same-lineage 0.15   cross-lineage 0.20
```

Derived from `result.json`:

```
key pair unions
  qwen3.5:4b + sorc/…q4:4b    18/18 = 1.00
  qwen3.5:4b + llama3.2:3b    18/18 = 1.00
  qwen3.5:4b + qwen3.5:9b     17/18 = 0.94   <- both already warm
  qwen3.5:4b + gemma3:4b      16/18 = 0.89

what each adds beyond the incumbent (qwen3.5:4b, misses {7,14})
  qwen3.5:9b                   adds [7]
  sorc/…q4:4b                  adds [7, 14]
  llama3.2:3b                  adds [7, 14]
  qwen2.5:7b, gemma3:4b        adds []      (both just worse extractors)

disagreement rate vs the incumbent (raw value-sets differ)
  qwen3.5:9b 0.17 · sorc/…q4:4b 0.28 · llama3.2:3b 0.39 · qwen2.5:7b 0.44 · gemma3:4b 0.44
```

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — the incumbent misses ≥ 1 in 10** | **holds** | 2/18 = 11% missed |
| **H2 — the union beats the best single by ≥ 5 pts** | **holds** | 1.00 vs 0.89 (+11) |
| **H3 — cross-lineage overlap < same-lineage** | **fails** | cross 0.20 > same 0.15 |
| **H4 — disagreement tolerable** | **unresolved** | 0.17–0.44; see below |

## What the numbers actually say

- **The gap is real and small.** The incumbent misses 2 of 18 — one of them (`7`, the
  trip's month) is missed by *every* competent model, so it is likely a labelling or
  schema artefact, not a model failure.
- **A council closes it — but the diversity is *within* the Qwen lineage.** The pair that
  reaches 1.00 is the incumbent plus `sorc/qwen3.5-claude-4.6-opus-q4:4b`, a Qwen-based
  fine-tune **already on disk**. The two *pulled* cross-lineage models did not add
  diversity: `llama3.2:3b` ties the fine-tune (adds the same two turns) at nearly twice
  the latency, and `gemma3:4b`/`qwen2.5:7b` are simply worse extractors — their "unique"
  catches are empty, so their low overlap is incompetence, not healthy disagreement.
- **H4 is not cleanly answered.** 0.28 for the best pair is a *raw value-set* difference,
  which counts phrasing differences ("6" vs "six strings") as disagreement. The true
  factual disagreement is lower; the number as measured is an upper bound.

## Threats and limits (what remains)

- **18 turns is a probe**, and one of the incumbent's two misses (`7`) is missed by all
  competent models — so the effective gap may be **1 fact in 18**, not 2.
- **Value-level recall is generous** (frame/key ignored). Schema-level recall is 0.28–0.39
  for *every* model, i.e. the schema is a shared weak point, not a council target.
- **Cross-lineage was tested with two models**, both worse extractors than the Qwen set.
  A *competent* cross-lineage model was not tested, so "lineage does not help" is
  supported for these two models, not for the hypothesis in general.
- **One run per model.** No temperature variance was sampled (extraction runs at
  temperature 0, so this is minor).

## Addendum: the parallel arm

```
[qwen3.5:4b_alone    ] median   5134ms  loaded_after=3 [qwen3-embedding, qwen3.5:9b, qwen3.5:4b]
[sorc_q4_4b_alone    ] median   6148ms  loaded_after=3 [qwen3.5:9b, qwen3-embedding, sorc/q4:4b]
[best_pair_parallel  ] median   7228ms  loaded_after=3 [qwen3.5:9b, qwen3.5:4b, sorc/q4:4b]
[warm_pair_parallel  ] median  12349ms  loaded_after=3 [qwen3.5:9b, qwen3-embedding, qwen3.5:4b]

incumbent alone      5134ms
slower member alone  6148ms
pair in parallel     7228ms   (serial sum would be 11282ms)
warm pair in parallel 12349ms
```

**H5 holds.** The pair costs **7,228 ms** against a serial sum of 11,282 ms — the slower
member (6,148 ms) plus 18%. Ollama does run two different models concurrently, and the
council is therefore near-latency-neutral versus doing the two calls in sequence.

**H6 holds for residency, but not for latency.** The warm pair leaves the loaded set
untouched (`{qwen3.5:4b, qwen3.5:9b, qwen3-embedding}` — the warm set), so its residency
cost is zero — but it is **slower** (12,349 ms), because `qwen3.5:9b` is the bigger model.

**The eviction is visible.** `sorc/q4:4b` run alone loaded `{qwen3.5:9b, embedding,
sorc/q4:4b}` — it **evicted `qwen3.5:4b`**. Every arm reports `loaded_after=3`, i.e. the
cap is saturated and a fourth model is not possible without evicting a warm one.

The parallel run's timings are also the better latency estimate: the sequential run's
15,540 ms median for `qwen3.5:4b` was cold/contended; alone it is 5,134 ms.
