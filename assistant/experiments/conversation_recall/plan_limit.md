# Round 2: does a wider episode-retrieval window recover the lost answers?

**Status:** pre-registered before data collection. Round 1 is `plan.md` /
`result.md`.

## What round 1 established

Baseline recall on facts older than the six-message history was **0.60**, and on
facts that live only in an old episode **0.333**. The failure was not a refusal
(0–7%) and not missing embeddings (802/802 embedded): the five retrieved
"Related past conversations" were mostly **questions about the topic**, not the
turn holding the answer. For the Instagram question the five slots went to two
questions about follower counts, one Spotify figure, and an apology — the turn
saying "296 followers" was not among them.

`retrieval_episode_limit = 5`.

## Hypothesis

The answer is being cut off by the slot count, not by similarity alone. Raising
the limit to **12** puts it in the window and recall rises.

**H1.** Episode-question recall rises from 0.333 to ≥ 0.80.

**H2.** Summary-question recall stays at 1.000 — a wider episode window must not
crowd out what already worked.

**H3.** Prompt cost rises, but within a turn's room: the extra turns are episode
digests capped at `max_episode_digest_chars = 240`, so seven more is ~1.7k chars.

## Variables

| | retrieval |
|---|---|
| A | `retrieval_episode_limit = 5` (production today) |
| B | `retrieval_episode_limit = 12` |

Same five questions, same session (`conv_f8df1353725c`), same model, temp 0.7,
think off, search and tools off, retrieval computed per condition and reused
across replicates.

## Metric

- `fact_in_context` — **new, and the manipulation check**: does the gold token
  appear in the retrieved material the model is given? This separates "retrieval
  never found it" from "retrieval found it and the model ignored it". Without it,
  a null at 12 would be ambiguous.
- `found` — the gold token appears in the answer.
- `declined`, `prompt_tokens` — as round 1.

## Falsification (decided before the run)

- **H1 null** if episode recall at 12 is within 0.10 of 0.333. Then the slot count
  is not the constraint and the small retrievable set is — no further constant
  tuning.
- **H1 partial** if `fact_in_context` rises but `found` does not: retrieval would
  be fixed and the model would be ignoring the material, which is a different
  problem and a different fix.
- **H2 null** if summary recall drops below 1.0 at 12, meaning the wider window
  displaces something that worked.
- If the digest cap (240 chars) is what hides the fact, `fact_in_context` stays
  false even at 12 and the next variable is the digest length, not the slot count.

## Run

```bash
docker run --rm -v "$(pwd):/app" -w /app \
  -v mydigitalassistantai_assistant-data:/brain:ro \
  -e DATABASE_PATH=/tmp/brain.db -e DB_KEY=… \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant sh -c 'cp /brain/assistant.db /tmp/brain.db && \
    python -m assistant.experiments.conversation_recall --limits'
```
