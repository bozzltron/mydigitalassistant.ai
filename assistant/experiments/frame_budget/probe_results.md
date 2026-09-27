# Probe results (pre-experiment measurements)

Two probes run before the experiment was designed. Both changed what the
experiment measures, so both are recorded here.

## Probe 1 — how many frames does retrieval actually return?

Default `Retriever` (`top_k_direct=3`, `graph_hops=2`, `graph_decay=0.5`,
`min_relevance=0.3`) against the verified copy of the live brain, 21 queries
spanning exact frame names, broad topics, and everyday chatter.

| query | frames | slots | context chars |
|---|---|---|---|
| Why Not | 3 | 2 | 2739 |
| The Mountain and The Wolf | 3 | 3 | 3100 |
| album my wife is in | 3 | 0 | 2945 |
| Friend Music Records | 3 | 3 | 3386 |
| what do you know about my music | 3 | 4 | 3553 |
| what did I do yesterday | 3 | 0 | 2611 |
| who is my wife | 3 | 1 | 2571 |
| my projects | 3 | 1 | 2659 |
| Tell me about the dog | 3 | 1 | 3095 |
| lunch | 3 | 3 | 2728 |
| guitar | 3 | 0 | 2842 |
| coffee | 3 | 3 | 2604 |
| the label | 3 | 0 | 2524 |
| my morning briefing | 3 | 10 | 4355 |
| reminder | 3 | 7 | 4274 |
| schedule | 3 | 1 | 2863 |
| what did we discuss about the label | 3 | 0 | 2563 |
| anything about music | 3 | 0 | 2950 |
| favourite album | 3 | 1 | 3113 |
| where do I live | 3 | 4 | 2864 |
| my family | 3 | 0 | 2512 |

**frames returned: min 3, median 3, max 3. Cap 5. Times the cap bound: 0 / 21.**

Two conclusions:

1. `max_frames_in_prompt` is a dead knob. Raising it from 5 to 40 would change
   nothing, because retrieval never returns a fourth frame. An experiment varying
   that setting would have returned byte-identical prompts at every level and
   concluded, falsely, that frame count does not matter.
2. The binding constraint is `top_k_direct = 3`. That is the variable the
   experiment varies.

Note also `slots = 0` on eight of 21 queries: the three retrieved frames were
often irrelevant to the question, so the agent had no slot content to answer from
at all. That is the recall failure this experiment measures.

## Probe 2 — is the graph walk firing?

`_graph_walk` scores each edge as

```
score = relevance x decay x assoc.confidence x assoc.priority
            x neighbor.confidence x neighbor.priority
```

and keeps the edge only if `score >= min_relevance` (0.3). With `decay = 0.5` and
`relevance = 1.0` at hop 1, the **maximum attainable score is 0.5**, and the four
remaining factors must multiply to at least 0.60 — meaning each must average
about 0.88 if they are equal. Association and frame confidences in this brain sit
near 0.5–0.8.

Measured over every edge from the top seed of 5 queries:

| metric | value |
|---|---|
| edges examined | 14 |
| edges that cleared the gate | **0** |
| min score | 0.0312 |
| median score | 0.0380 |
| max score | 0.0621 |
| gate | 0.3000 |
| gate / observed p90 | **6.7x** |

**The graph walk has never contributed a frame in production.** The association
graph is rich — `why_not` has 39 live one-hop neighbours and 131 at two hops — and
all of them are embedded, but the multiplicative decay makes the relevance gate
unreachable, so none of it is ever read.

This is the direct cause of the "I don't have that" failure mode: the agent cannot
traverse to related memory even when the edges are all there.

## Why this is not fixed inside this experiment

`graph_decay` and `min_relevance` are retrieval variables. Changing them here would
change two things at once and the frame-budget effect would become uninterpretable.
The experiment is scoped to the prompt budget alone; the graph walk gets its own.

Recorded as a follow-up: the gate is unreachable by construction, and the fix is
either a separate gate for graph edges (association structure is not a similarity
score and should not be multiplied into one) or a non-multiplicative combination.
