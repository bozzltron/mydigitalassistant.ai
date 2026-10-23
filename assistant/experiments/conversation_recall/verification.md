# Verification

Checked before `result.md`.

## 1. The two conditions differ only by the summary

`main()` computes `memory_text = format_memory_context(context)` **once per
question** and reuses it for both conditions; `summary` prepends
`## This conversation so far\n<summary>` to that same string. Retrieval cannot
differ between A and B, by construction.

## 2. The store was a copy, not the live brain

The harness ran in a throwaway container against `cp /brain/assistant.db
/tmp/brain.db`, with the live volume mounted **read-only**. Even an unintended
write could not touch prod. (Stronger than the plan's "read-only by discipline".)

## 3. The summary was already in the prompt

`summary_already_retrieved: true` — for the summary-type questions the
`conversation_summary_conv_f8df1353725c` frame was **already** among the retrieved
frames, and `format_memory_context` renders it under `## Conversation summaries`.
So condition B added a second copy of a section the model already had. That is the
finding, not a defect in the manipulation: the injection mechanism was measured
against a prompt that already contained the summary.

## 4. Grading is deterministic and forgiving in the hypothesis's favour

Gold tokens are matched case-insensitively with commas stripped (`1,008` ≡
`1008`). `declines` is recorded separately because "I don't have that" is a
different failure from a confident wrong answer. One refusal occurred (B, one
replicate); it is reported rather than folded into recall.

## 5. Coverage of the mechanism

- 802 episodes in the session, **802 with embeddings** for the current model
  (100% coverage) — the retrieval gap is not missing vectors.
- `retrieval_episode_limit = 5`, `retrieval_min_distance = 0.7`,
  `max_episode_digest_chars = 240`.

## 6. Raw retrieval inspected, not just the answers

For each failing question, the retrieved `past_conversations` were printed:

**"How many Instagram followers does Mozworth have?"** — top 5:

```
0.81 user       "Can you update follower counts on mozworth to see any recent growth?"
0.79 user       "Actually, on spotify, mozworth has 33 monthly listeners. 82 followers."
0.78 assistant  "Instagram is hard-blocked from scraping, and the search results keep…"
```

The turn that contains the answer — *"We have 296 followers on instagram"* — is
**not in the top five**. The ranking prefers turns that *ask about* follower
counts.

**"How did we split the email broadcast segments?"** — top 5 are four questions
about the broadcast and one MailerLite note; the turn describing the segment
split is absent.

That is the measured cause: **retrieval finds the topic and ranks the questions
about it above the turn that states the fact.**

## 7. No answer was written to memory

The harness never constructs an `Orchestrator`, so `create_episode` is never
called. The 30 generations are stateless.
