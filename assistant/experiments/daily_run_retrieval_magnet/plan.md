# Experiment: Are `daily_run_*` Frames a Retrieval Magnet?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this document is
committed.**

## Question

On the 09:13 turn ("Sort the whole list of links in order of impact for mozworth"), retrieval
logged:

```
Retrieved daily run frames: ['daily_run_2026_08_28', 'daily_run_2026_09_16']
```

Neither is related to the user's submission links. Their only slots are `date`, `tasks_run`,
and `status` — near-identical across every daily run — so they may cluster tightly in
embedding space and act as a magnet for short keyword queries.

This was originally drafted as a phase of Plan A ("exclude `daily_run_*` from direct vector
candidates"). It is moved here because it is a **measurement question with a falsification
condition**, not an implementation step — the project's own convention.

## Why this is worth measuring

The observation is a single logged retrieval, and a single example is not a pattern. If
`daily_run_*` frames are reliably crowding the prompt, they are displacing the user's own
content — which is the failure Plan A addresses, from a different direction. If they are
not, changing retrieval ranking would be an unmeasured intervention on the hot path, against
the rule "measure before changing the hot path".

## Hypotheses

- **H1 (frequency).** `daily_run_*` frames appear in the top-N candidate set for a
  non-trivial share of queries that have nothing to do with scheduled runs.
- **H2 (similarity).** Their similarity to such queries is low, meaning they are admitted by
  the graph walk or by a low bar rather than by genuine semantic match.
- **H3 (displacement).** When they appear, they cost a slot that a more relevant frame would
  otherwise occupy, and the fit drops a frame it would not otherwise drop.
- **H4 (query shape).** The effect is concentrated in short keyword queries, not in longer
  natural-language ones.

## Variables

- **Condition A (control):** production retrieval, unmodified.
- **Condition B:** `daily_run_*` excluded from *direct* vector candidates, still reachable
  via associations and semantic episode match.
- Reported per condition: frames returned, frames rendered, `daily_run_*` count and share,
  frames dropped by the fit, memory chars, and latency percentiles.

## What counts as evidence

- **H3 is the primary test.** H1 and H2 without H3 means the frames appear but cost nothing
  — interesting, not actionable.
- If `daily_run_*` are under **5%** of delivered frames on unrelated queries, the magnet
  hypothesis is not supported and no ranking change is warranted.
- Latency must be compared, not assumed: excluding candidates could change the fit's work.

## Falsification conditions

1. *`daily_run_*` are rare in delivered context* → the magnet is a single observation; drop
   the hypothesis and change nothing.
2. *They appear but never displace a frame* → no user-visible cost; not worth a ranking
   special-case.
3. *They are admitted by strong similarity* → they are genuinely matching, and the problem
   is the embedding text (`date`/`tasks_run`/`status`), not the candidate filter — a
   different fix entirely.
4. *Excluding them also removes legitimate hits* (e.g. "what did my morning runs cover?")
   → the exclusion must be query-conditional, and that complexity has to be justified by H3.

## Safety

Read-only, against a **copy** in a scratch volume, exactly as `graph_walk_yield`:

- The live volume is not mounted into the experiment container.
- `experiments/preflight.py` gates on: experiment DB ≠ live DB, live volume not mounted, a
  restorable backup exists, and the copy's SHA-256 recorded.
- Table digests before and after, written to `result.json`.
- `verification.md` before `result.md`, naming remaining threats.

## Known threats, stated now rather than after seeing results

1. **One logged example motivated this.** The hypothesis could be over-fitted to the 09:13
   turn. The query set must be drawn independently, not from that turn.
2. **Retrieval is not bit-reproducible.** `graph_walk_yield` measured a ~2% spread from
   Ollama's non-deterministic embeddings; per-query frame counts carry a couple of frames of
   jitter, so any A/B must check direct-search drift rather than attributing it to the
   variable.
3. **Excluding candidates changes the fit**, so H3 can be confounded with the budget rather
   than caused by displacement.
4. **One corpus, one owner.** `daily_run_*` density grows with scheduler use; this brain's
   density may not be typical.
