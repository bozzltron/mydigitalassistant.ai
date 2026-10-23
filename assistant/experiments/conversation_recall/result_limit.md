# Round 2 result, and a correction to round 1

## The limit change

`retrieval_episode_limit` 5 → 12, same five questions, three replicates:

| | recall (all) | episode questions | fact in context | prompt tokens |
|---|---|---|---|---|
| limit 5 | 0.600 | 0.333 | 0.800 | 6,151 |
| limit 12 | 0.600 | 0.333 | 0.800 | 6,598 |

**H1 falsified.** A wider episode window changes nothing: the same facts are in
context, the same one is not, and 447 extra prompt tokens buy nothing. The slot
count is not the constraint. `plan_limit.md`'s falsifier said to stop tuning this
constant, and this is that stop.

## Why, and a correction

The `fact_in_context` check separated two failures, and inspecting both showed the
round-1 conclusion was wrong.

**"Why Not" release date** — `fact_in_context=True`, model answered **October 22**.
That is **correct**: episode 2026-10-18 says *"Your 'Why Not' single releases in 4
days (October 22)"*. My gold (`Oct 1`, from the first exchange) was the original
plan, not the current truth. Harness error, scored as a failure.

**Instagram followers** — the model answered **292**. That is not a retrieval
ranking artefact; it is a **stale slot**. The same fact is stored on three frames:

| frame | key | value | updated |
|---|---|---|---|
| `instagram_followers` (concept) | `count` | **296** | 2026-10-12 |
| `instagram` (entity) | `followers` | **296** | 2026-10-08 |
| `mozworth` (entity) | `follower_count_insta` | **292** | 2026-08-28 |
| `mozworth` | `instagram_followers` | **172** | 2026-08-28 |
| `mozworth` | `followers` | **5** (Bandcamp's count) | 2026-10-10 |

The newest and correct value (296) is on the *metric* frames. The question names
**Mozworth**, so retrieval returned the band frame and the model read 292 from it.
`fact_in_context=False` confirms the 296 frames were not in the prompt.

Corrected recall: **episode questions 0.667, overall 0.80**. The one failure is
**memory fragmentation plus frame selection**, not a lost conversation.

## What this means

- The six-message history window is real, but it did **not** cause either failure:
  both facts were in memory, and one was answered correctly from it.
- The cause is that **one attribute is stored under three keys on three frames**,
  so the confidence ladder never compares the values (it compares within a key),
  and the frame the question names is the one holding the stale value.
- Extraction noise is visible in the same frame: `follower_count = 'recent_growth'`
  (a phrase, not a count) and `followers = 5` (Bandcamp's number, detached from its
  subject).

## Open question this raises

Consolidation merges near-duplicate frames every six hours. `instagram` and
`instagram_followers` are near-duplicates by name and should have been candidates;
they were not merged. Whether the threshold missed them or merging metric frames is
undesirable is the next thing to measure — not the history window, and not the
retrieval limit.
