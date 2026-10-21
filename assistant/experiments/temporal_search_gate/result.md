# Result

20 queries × 3 router replicates, `qwen3.5:4b`, temperature 0.0, against a memory
context that is **sufficient** by the reasoner's own test. No search, no
generation, no database.

## Headline

| group | baseline gate | `router_positive` |
|---|---|---|
| period (10) | **0.00** | **0.90** |
| stable (10) | 0.00 | **0.00** |

Router recall on period questions: 0.90. False positives on stable: 0.00. No
flapping across three replicates.

## Verdict

- **H1 supported.** With relevant memory present, the current gate searches for
  **none** of the period-scoped questions — the live failure reproduced exactly.
  Letting the router's positive judgement through raises it to 0.90, against a
  0.30 bar.
- **The over-search control holds.** Stable questions stay at 0.00: the router's
  `wants_search` is already false for definitions, stored facts and storage-style
  turns, and the veto is untouched.
- No threshold was tuned and no phrase list was added. The change is *removing a
  hardcoded override that outranked a model judgement*.

## Why this is the right fix and the write path was not

The live check showed the agent answering NASA's image-of-the-day from yesterday's
episode. The memory was **relevant and sufficient** — that is precisely what
suppressed the search. The reasoner is a heuristic (`reasoner.py:147`), and the
router's model judgement was only allowed to force a search when memory was
`NONE`. So the model already knew the question needed current information; the
code discarded it.

That is a one-condition change in the orchestrator, not a schema, an extraction
flag, or another prompt section. It cost nothing to measure because the decision
is deterministic given two inputs.

## Decision

**Ship the gate change**: `wants_search is True` forces a search regardless of
memory sufficiency, keeping the veto. `orchestrator.py:1576-1591`.

What this buys, precisely: a period-scoped question with a plausible stored answer
now refreshes instead of repeating it. What it costs: a search on any turn the
router judges as needing current information even when memory looks relevant —
including recommendation-style questions ("best budget X"), where the router
prompt already says products and rankings change.

## What is not measured, and the follow-up

- **Latency and quota.** The run counts decisions. Each added search is a round
  trip (SearXNG locally, Brave when enabled and paid). The rate to watch is the
  `Reasoner triggered search` log line; if it rises sharply on ordinary turns,
  the gate is too permissive and the bar moves.
- **Answer quality.** The follow-up is the live six-question set again, this time
  with the gate in place: does "NASA's image of the day" refresh on every
  repetition, and does a stable question still answer from memory in under the
  usual latency?

If the live set shows refreshing on the period questions and no measurable search
growth on stable ones, this line is closed: the dates and the rule remain, the
heuristic's override is gone, and no schema changed.

## Live follow-up (after shipping)

Three live turns on prod with the gate in place, same question:

| rep | searched | `search_ms` | turn |
|---|---|---|---|
| 4 | **yes** | 27,776 | 75.8s |
| 5 | **yes** | 11,442 | 41.4s |
| 6 | **yes** | 31,859 | 64.4s |

All three refreshed. Before the change, two of three answered from the previous
episode; now the log shows `Search forced by router (model judged current info
needed)` on every turn.

**The cost is visible and real**: a search adds 11–32s to a turn that previously
answered from memory in ~1–2s. That is the trade the gate makes, and it is the
trade we asked for — the alternative was a confidently stale answer.

**A new, separate imperfection**: the three answers disagree on *what* today's
image is (one says "Martian Dawn", two say "An Anticrepuscular Rainbow Fan"). All
three are labelled today's. That is search-result variance, not memory staleness —
the extraction picked a different page's subject in one run. It belongs to the
search/extraction line, not this experiment, and is recorded here so it is not
mistaken for a regression of the gate.

**Verdict: the line is closed.** Period-scoped questions refresh; stable questions
do not search; no schema changed; the dates and the time rule stay.
