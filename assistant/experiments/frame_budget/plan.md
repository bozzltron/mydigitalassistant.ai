# Experiment: Memory Frame Budget in the System Prompt

## Question

How does the number of memory frames injected into the system prompt affect
(a) whether the agent can answer questions about the user's own stored knowledge,
and (b) what it costs in latency and prompt tokens?

## Why this experiment exists

The frame budget was chosen by default, not measurement. `max_frames_in_prompt = 5`
in `backend/config.py:133` and `top_k_direct = 3` in `Retriever.__init__` were never
tuned. A prior investigation found "Why Not" ranked 457th of 1992 for a query naming
it exactly, which motivated frame chunking — but chunking changes how a frame is
*represented*, not how many frames the agent is *allowed to see*. This experiment
measures the second thing.

## Preliminary finding that reshaped the design

Before the experiment was written, a probe measured how many frames retrieval
actually returns (`assistant/experiments/frame_budget/probe_results.md`):

| queries probed | frames returned | cap | times cap bound |
|---|---|---|---|
| 21 | **3 for every single query** (min 3, median 3, max 3) | 5 | **0 / 21** |

`max_frames_in_prompt` is therefore a **dead knob**: at 5, 10, 20 or 40 the rendered
prompt is byte-identical, because retrieval never returns more than 3 frames. An
experiment varying it would have produced identical outputs at every level and
"discovered" that frame count does not matter. That is a false negative produced by
a no-op manipulation, which is exactly the failure this experiment is built to avoid.

The binding constraint is `top_k_direct = 3` in `Retriever.__init__`.

A second probe found the graph walk contributes **zero** frames, always. It scores
each edge as `relevance × decay × assoc.confidence × assoc.priority ×
neighbor.confidence × neighbor.priority` and gates on `min_relevance = 0.3`. With
`decay = 0.5` the maximum attainable hop-1 score is 0.5, and the four remaining
factors must multiply to ≥ 0.60 — each averaging ≈ 0.88 if equal. Measured
distribution over the top seeds' edges: min 0.031, median 0.038, max 0.062.
**0 of 14 edges cleared the gate; the threshold sits 6.7× above the observed p90.**

So the agent has never traversed memory in production. Both facts are recorded here
because they determine what the independent variable actually is.

## Hypotheses

- **H1 (accessibility).** Gold-fact recall is ≈0 while the frame holding the fact is
  outside the budget, and rises to ≈1 once the budget includes it. Recall should
  therefore track the target frame's retrieval rank, producing a dose-response curve.
- **H2 (abstention).** The rate of "I don't have that in memory" responses falls as
  budget rises. This is the user-visible failure mode, not an internal metric.
- **H3 (cost).** Prompt tokens and latency increase monotonically with budget.
- **H4 (saturation).** Recall plateaus once the budget exceeds the target rank;
  further frames add cost without adding recall.

## Variables

**Independent** — frames present in the memory section of the system prompt:
`0, 1, 2, 3, 5, 10, 20, 40`.

Budget 0 is the **negative control**: the model provably cannot see any frame, so
any gold fact it still produces came from pretraining, not memory.

**Dependent**
- `gold_recall` — fraction of gold facts present in the answer (primary)
- `abstention_rate` — fraction of answers that decline for lack of memory
- `sys_prompt_chars`, `memory_chars`, `truncated` — prompt cost and ceiling effects
- `prompt_tokens`, `eval_tokens` — reported by Ollama
- `latency_ms` — end-to-end generation time

**Controls** (held identical across every condition)
- **One retrieval per query.** The candidate pool is retrieved *once* at
  `top_k_direct=40`, then truncated to each budget. Every condition sees the same
  ranked frames, so retrieval variance and the 236ms search cost are removed from the
  comparison. Because the graph walk contributes 0 frames (measured above),
  truncating the pool is exactly equivalent to running the production path at
  `top_k_direct = budget`.
- Chat model, `temperature = 0.7` (production default), `think = False`
  (`chat_think_default`), same `build_system_prompt`, same episodes, same user.
- Conditions are **interleaved and order-rotated** within each replicate, so thermal
  drift or a cold Ollama cannot load onto one condition.
- Replicates: 3, because the chat model is a thinking-capable model at temperature 0.7
  and is not deterministic.

## Stated scope limit

This experiment measures the effect of *how much of the retrieved set is shown*.
It does **not** measure the effect of changing retrieval itself (`top_k_direct`,
`graph_hops`, `graph_decay`, `min_relevance`). Those are separate variables and the
dead graph walk in particular deserves its own experiment. Fixing retrieval and then
re-running this one is the planned follow-up.

## Gold facts and why they are screened

Gold facts are read from the database, never from model output, and are only kept if
they are **memory-dependent**: the fact must be missed at budget 0 and found at
budget 40. This screening step is load-bearing — facts the model already knows from
pretraining (CBS News was founded in 1927, for instance) would otherwise inflate
recall at every condition and hide any real effect. The retained set is therefore
provably answerable only from memory.

Grading is exact substring matching on normalized text against a list of accepted
surface forms per fact. It is blind to condition, needs no LLM judge, and cannot be
gamed by a longer answer.

## Success criteria

- [ ] Every condition's rendered context contains exactly the requested number of
      frames (manipulation validity; asserted, not assumed)
- [ ] `gold_recall` at budget 0 is ≈0 on the retained fact set
- [ ] `gold_recall` rises with budget and tracks target rank (H1)
- [ ] `abstention_rate` falls with budget (H2)
- [ ] Latency and prompt tokens increase with budget (H3)
- [ ] A curve that plateaus rather than continuing to rise supports H4
- [ ] No database mutation during the run

## Falsification

This experiment reports a null result if `gold_recall` does not track budget. The
pre-registered ways that can happen, each of which would be reported rather than
explained away:
1. The target frame is in the top 3 for every query, so budget ≥3 changes nothing.
2. The `max_system_prompt_chars = 12000` cap truncates the memory section before the
   budget binds, so large budgets are not actually larger.
3. Model temperature 0.7 noise exceeds the effect size.
4. The answer comes from `recent_episodes` rather than frames, making the frame
   budget irrelevant.

The run records `frames_rendered`, `memory_chars` and `truncated` for every
generation specifically so that (1) and (2) are visible in the raw data rather than
inferred.

## Run

```bash
docker run --rm -i --network mydigitalassistantai_appnet \
  --add-host host.docker.internal:host-gateway \
  -v "$(pwd):/app" -w /app -v frame-budget-exp:/exp \
  -e DB_KEY="$(grep '^DB_KEY=' .env | cut -d= -f2-)" \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  mydigitalassistantai-assistant python -m assistant.experiments.frame_budget
```

Runs against `frame-budget-exp`, a verified byte-identical copy of the live brain.
The live database is never opened for writing.
