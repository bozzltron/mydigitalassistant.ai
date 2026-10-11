# Experiment: is the brain a model of the user, or a cache of the internet?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The project's thesis is to build a model of the user's world by **triangulating the user
and the internet**. If the memory is mostly internet-derived, then what is actually being
built is a search cache with a user attached, and the thesis is not the thing in the
database.

**What fraction of memory is user-sourced vs internet-sourced?**

## Definitions (fixed before the data)

Every frame and every slot carries a `source_type`. The conversational extraction path
passes **none** (`orchestrator.py:2167` calls `extract_and_apply` without one), so a NULL
`source_type` *is* the user's conversation, just unlabelled.

- **internet** — `search`, `web_fetch`.
- **user** — NULL (conversation), `user`, `user_correction`, `user_supplied`,
  `conversation`, `correction`, `file_upload` (content the user supplied).
- **derived** — everything else: files the *agent* wrote, CSV rows, scheduled tasks,
  alerts, summaries, computation.

Measured over **live frames** and over **slots** (the facts) separately, because a frame
count and a fact count are different questions.

## Hypotheses

- **H1.** The internet is the **largest single source** of live frames.
- **H2.** The user's own contributions are a **minority** of slots.
- **H3.** A **non-trivial share carries no provenance at all** (NULL), so "user" cannot be
  counted from labels alone.

## Falsification conditions

1. `user` ≥ `internet` on slots → the thesis holds as built; **stop**, there is nothing to
   fix.
2. `internet` < 40% of slots → the internet is **not** crowding the user out.

## What counts as evidence

The **share of slots** is the decision number (slots are the facts; frames are containers).
The frame share and the NULL share are reported alongside, because they say *how* the
split is shaped, not just how big it is.

## Method

A read-only query of a brain, grouped by `source_type`, no writes — the `brain_health`
pattern. Run against **both** dev and prod, since they have different histories.

```bash
docker cp assistant/experiments/memory_source_split/experiment.py assistant-backend:/tmp/mss.py
docker exec assistant-backend python /tmp/mss.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- **Provenance is not aboutness.** A `search` fact can be about the user's band; a
  conversational fact can be about the world. The split is a proxy for **who stated it** —
  which is exactly the triangulation axis the project names — not for what it concerns.
- **A brain's history is not a rate.** It reflects which features were exercised, not what
  the design intends.
- **NULL is inferred.** The chat path passes no `source_type`; if some *other* writer also
  omits it, the NULL bucket is not purely conversational. The sample inspection in
  `verification.md` checks this.
- Counts include `derived` internal frames (task bookkeeping, alerts) that are neither the
  user's facts nor the internet's.
