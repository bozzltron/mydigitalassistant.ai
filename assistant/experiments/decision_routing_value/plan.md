# Experiment: Does a Typed Decision Model Route Better Than Free-Form Choice?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

On 2026-09-30 the user pasted 45 submission URLs and asked for them ranked. The
list was registered as memory and injected into the prompt with an explicit
instruction to work from it — and the tool loop still called `web_search`, per link,
and the answer became an essay about one station.

Instrumentation settled where the fault is:

```
15:09:12  search_ms=0        <- the router did NOT plan a search (routing fix works)
15:08:52  Brave: "WPRB executive director contact indie alt rock submissions impact"
                             <- the MODEL chose web_search inside the tool loop
```

So the pipeline is correct and the **tool choice** is wrong. That choice is a typed
decision — *given supplied content and a transform request, answer from the content
or search first?* — and we are currently asking a general generation model
(`qwen3.5:9b`) to make it as a side effect of writing prose.

Ollama 0.35 ships `/v1/systemone`: text as `state`, named questions, one typed
answer per question, with per-answer `confidence`. The blog names model routing as
a target use case, which is what this is.

**This experiment measures whether a decision model routes a transform turn better
than the generation model chooses, and at what latency cost.**

## Why this is worth measuring

Three plausible outcomes, all worth knowing before writing code:

1. The decision model routes correctly and cheaply → adopt it for this decision.
2. It routes no better than the generation model → the failure is elsewhere
   (prompt, tool availability), and we do not add a model to the hot path.
3. It routes well but costs more than the turn it protects → not adoptable, and we
   fall back to withholding `web_search` for these turns.

The third is the one a feature-first reading would miss.

This also de-risks Plan D: a conflict verdict (`ADOPT_EXISTING | ADOPT_NEW | MERGE |
ESCALATE`) is the same shape of decision, and this experiment establishes whether
the endpoint is usable for that class at all.

## Hypotheses

- **H1 (accuracy).** On labelled turns, `/v1/systemone` picks the correct tool more
  often than `qwen3.5:9b` does in the current loop.
- **H2 (latency).** The decision costs materially less than the search it prevents.
  A search-driven turn measured 40.0s end to end; the blog's own figure is ~91ms
  per decision on an M-series host. The bar is stated in advance below.
- **H3 (calibration).** The reported `confidence` is informative: low-confidence
  decisions coincide with the cases a human would call ambiguous. If it is not, the
  confidence field cannot be used as an ambiguity signal, which matters for Plan D.
- **H4 (model size).** The 4B model is not materially worse than the 9B on this
  task, so the cheaper model suffices.

## Variables

Conditions, run over the same labelled turns:

- **A (control):** current behaviour — `qwen3.5:9b` chooses freely among the real
  tool list, as production does.
- **B (`tev1:0.8b`):** `systemone` choice question, `{answer_from_content,
  search_to_enrich, search_instead}`.
- **C (`tev1`, 4B):** same question, same model family, larger.
- **D (`nimble`, 9B):** same question, the benchmarked model.

This gives the model-size comparison (H4) on one question, so the only thing
varying between B/C/D is the model.

Reported per condition: chosen route vs. the label, accuracy, latency percentiles,
and the reported confidence distribution. Condition A is measured as *the tool the
model actually called first*, not as a re-asked question — the point is what
production does.

## The labelled set

Turns are authored to cover the route boundary, including the cases designed to be
*tempting to search*:

| # | Turn | Correct route |
|---|---|---|
| 1 | The real 45-URL paste + "rank them by impact" | answer_from_content |
| 2 | 7-URL paste + "sort these in order of impact" | answer_from_content |
| 3 | 7-URL paste + "summarise these links" | answer_from_content |
| 4 | 7-URL paste + "which of these accept physical submissions?" | search_to_enrich |
| 5 | "what is the capital of France?" | search_instead |
| 6 | "search for austin music festivals" | search_instead |
| 7 | 5-URL paste + "rank these, and tell me anything notable about them" | search_to_enrich |
| 8 | "what do you remember about my guitar?" | (out of scope — no search) |
| 9 | 6-URL paste + a follow-up "now sort them" | answer_from_content |
| 10 | "how are you today?" | (out of scope — no search) |

Turns 8 and 10 are the negative control: the decision must not route them to
search. A router that answers "search" when nothing is being transformed is worse
than nothing.

Labels are authored **by the human**, before any model runs, and frozen in this
document. The decision models are not the labellers.

## What counts as evidence

- **H1 is the primary test.** The bar, fixed now: **the best decision model must
  route at least 8 of the 10 turns correctly, and must beat condition A by at least
  2 turns, or the experiment does not support adoption.**
- **H2:** a decision must cost **under 250ms** at p50 on this host. Above that, the
  cost on the hot path outweighs the search it prevents.
- **H3:** low confidence must concentrate on the ambiguous turns. If confidence is
  uniformly high, report it as uninformative and note what that costs Plan D.
- **H4:** if `tev1:0.8b` matches `tev1`/`nimble` within 1 turn, prefer the smallest.

## Falsification conditions

1. *Best decision model fails to beat A by 2 turns* → the typed-decision route does
   not address this failure; fall back to withholding `web_search` on transform
   turns, and say so.
2. *Decision cost exceeds 250ms p50* → not adoptable on the hot path regardless of
   accuracy.
3. *Small model matches the large ones* → adopting the 9B is unjustified on size
   alone.
4. *Models flag turns 8/10 as search* → the question is mis-specified, and the
   result is a prompt-design finding, not a model finding.
5. *The endpoint does not support the question shape* (e.g. no three-way choice, or
   `noul` semantics differ from the docs) → report the API gap rather than working
   around it silently.

## Safety and isolation

The brain is not involved, so this is isolated by construction rather than by copy:

- **No database access.** The experiment reads no brain, writes no brain, and does
  not import the store. There is nothing to mutate, so no `preflight.py` gate and no
  table digests are needed — and this is stated explicitly rather than left implicit,
  because every other experiment in this directory *does* open a brain and a reader
  will expect that here.
- **No retrieval, no orchestrator, no tool execution.** Condition A asks the model
  what it would call; it does not call anything. No search is issued, so no query
  text leaves the machine and Brave is never contacted.
- **Local inference only.** `/v1/systemone` on `127.0.0.1:11434`, per the project's
  hard rules.
- **The turns are synthetic.** They are authored here, not taken from the brain, so
  the experiment carries no personal data even in its fixtures.
- Results go to `result.json` and `result.md` in this directory only.

## Known threats, stated now rather than after seeing results

1. **Ten turns is a small set.** Enough to detect a large difference; not enough for
   a fine-grained accuracy claim. Any conclusion is reported with that limit.
2. **I authored both the turns and the labels.** The tempting-search cases (4 and 7)
   are my judgement of where the boundary lies. A different author would draw it
   differently, and the label set is the experiment's weakest link.
3. **Condition A is not a controlled ask.** It measures what `qwen3.5:9b` does in
   the real tool loop, with the real tool descriptions and prompt — which is the
   point, but it means A's behaviour depends on prompt text that may change. It is
   a snapshot, not a constant.
4. **Latency here is not latency in production.** A `systemone` call on an idle host
   with a warm model is not the same as one issued mid-turn competing with the warm
   set for memory. H2's 250ms bar is a floor test, not a prediction.
5. **Decision-model quality on this task is not established by the vendor's
   benchmark.** Their 3,880 decisions are ticket triage and moderation, not
   "should I search before ranking a list". The benchmark is why the models are
   worth trying, not evidence that they work here.
6. **A correct route is not a correct answer.** Routing right for turns 1–3 does not
   prove the final answer enumerates the list; that is Plan A's acceptance test, not
   this one.
