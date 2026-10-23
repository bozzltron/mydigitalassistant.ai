# Result

5 questions × 2 conditions × 3 replicates on `conv_f8df1353725c` (802 episodes,
the user's largest live session), `qwen3.5:9b`, temp 0.7, search and tools off.

## Headline

| condition | recall (all) | summary questions | episode questions |
|---|---|---|---|
| A — current prompt | **0.600** | 1.000 | **0.333** |
| B — + session summary | **0.600** | 1.000 | **0.333** |

Refusals: 0.0 / 0.067. Prompt tokens: 6,151 → 6,259 (+108).

## Verdict

- **H1 supported.** Baseline recall on facts older than the six-message window is
  **0.60**, below the 0.80 bar: two of five questions lose their answer.
- **H2 falsified — the summary injection does nothing.** B is identical to A on
  every question, because the summary frame was **already retrieved** for the
  questions it could answer (`summary_already_retrieved: true`). Injecting it adds
  a duplicate section, not new information. **Option 1 is dead** — not because
  summaries are useless, but because retrieval already delivers them.
- **The loss is on episode-only facts**, and it is a **retrieval ranking** problem.

## What actually fails, and why

For *"How many Instagram followers does Mozworth have?"* retrieval returns:

```
0.81 user       "Can you update follower counts on mozworth to see any recent growth?"
0.79 user       "Actually, on spotify, mozworth has 33 monthly listeners. 82 followers."
0.78 assistant  "Instagram is hard-blocked from scraping…"
```

The turn that states the answer — *"We have 296 followers on instagram"* — is not
in the top five. The model is shown three turns that *discuss* follower counts and
one wrong-platform figure, and answers from those or declines. The email-segment
question behaves the same way: its top five are all questions about the broadcast.

So the conversation does not "break" because the summary is missing. It breaks
because **similarity ranks a question about a topic above the turn that answers
it**, and `retrieval_episode_limit = 5` leaves no room for the answer to appear
anyway.

## The other half: the UI restore limit

Measured directly against the live brain: `GET /chat/session/…/messages` returns
**244** messages at `limit=300`; the frontend asked for **50** (`state/chat.ts:90`),
so on reload a 244-message conversation rendered its last fifth. Raised to **200**
with a named constant. Display-only — the model still sees six — but it is half of
the reported "things from earlier are gone", and it is the half that is
unambiguously a bug rather than a trade-off.

## What this points to next

The representation is not the lever; **retrieval is**. In order of what the
evidence supports:

1. **Raise `retrieval_episode_limit`** (5 → 10–15) and measure recall on the same
   five questions. Cheap, and the cost is visible in `prompt_tokens` (the episode
   digests are capped at 240 chars each).
2. **Diversify the hits.** The top five were near-duplicates *of the question*.
   MMR or a per-turn similarity cap would spend the five slots on five different
   turns instead of five paraphrases.
3. **Prefer turns that state a fact over turns that ask one.** That is a prior on
   role/content, not a keyword list — but it needs its own measurement before it
   earns a place.

The bar for (1): recall on the episode questions **0.333 → ≥ 0.80** without
dropping the summary questions below 1.0.

## Known limits of this run

- Five questions, one session. The session is the largest available, which is the
  right place to start, but five questions is a probe, not a benchmark.
- The 240-char digest cut was not varied. A fact beyond the cut in its turn would
  fail for a second reason this run cannot separate.
- The two "summary" questions were answered from a 509-char summary; a longer
  summary might have changed them, but they were already at 1.0, so there is no
  headroom to measure.

## Correction (after round 2)

Two of this run's gold facts were wrong, and the conclusion drawn from them does
not survive.

1. **"Why Not" release date.** I scored `Oct 1` from the first exchange. The date
   genuinely moved: episode 2026-10-18 says *"Your 'Why Not' single releases in 4
   days (October 22)"*. The model answered **October 22 — correctly**. My gold was
   stale, and the "found=False" for that question is a harness error, not a
   failure.
2. **Instagram followers.** The model answered **292**, and I read that as
   retrieval ranking the wrong turns. It is not: 292 is a **stale slot on the
   `mozworth` frame**, and the correct 296 lives on two *different* frames that
   retrieval did not return. See `result_limit.md` for the slot table.

Corrected: episode-question recall is **0.667** (2 of 3), overall **0.80** (4 of
5), not 0.333 / 0.60. The failures are memory hygiene, not a lost conversation.
