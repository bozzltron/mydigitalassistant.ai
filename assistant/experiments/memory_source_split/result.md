# Result — is the brain a model of the user, or a cache of the internet?

**A cache of the internet, with a user attached.** Across two independently-used brains,
the internet supplies **53–55% of the facts** and the user's own contributions **22–23%** —
the internet is **~2.3×** the user. And the user's share is **unlabelled**: it is the NULL
`source_type`, not a recorded provenance.

| | prod | dev |
|---|---|---|
| **slots** — internet | **52.8%** | **55.0%** |
| **slots** — user | 23.0% | 22.0% |
| **slots** — derived (the system's own machinery) | 24.1% | 23.0% |
| **frames** — internet | 65.7% | 66.7% |

## By hypothesis

| | Verdict |
|---|---|
| **H1 — the internet is the largest single source** | **holds** (65.7% / 66.7% of frames) |
| **H2 — the user is a minority of slots** | **holds** (23.0% / 22.0% vs 52.8% / 55.0%) |
| **H3 — a non-trivial share has no provenance** | **holds** (NULL ≈ 22% of frames) |

## Falsification conditions, resolved

1. `user` ≥ `internet` on slots → **not triggered**.
2. `internet` < 40% of slots → **not triggered** (53–55%).

Both brains agree within ~2 points, so **this is a property of the system, not of one
brain's history.**

## The finding

- **The thesis is not what is in the database.** The project is defined as triangulating
  the user and the internet. The internet is not one of two peers here; it is the **primary
  source, 2.3× the user**, and the user's side is the smaller half of a two-sided design.
- **The user's own facts are the unlabelled ones.** They arrive through the conversational
  extraction path, which records **no `source_type`**. So the answer to "what do you know
  about *me*?" cannot be answered from the labels — the user's memory is the bucket marked
  "unknown".
- **A quarter of memory is the system's own machinery.** `derived` (CSV rows, task runs,
  alerts, summaries) is **24%** of slots — *larger than* the user's share and nearly half
  the internet's. Bookkeeping is outgrowing the user.
- **275 conflicts are pending in both brains** — the same number, which suggests a
  standing backlog rather than a queue being worked.

## What this proves, and what it does not

- **Proved:** by the project's own definition of provenance, the user is a minority source
  and the internet dominates. The triangulation is lopsided.
- **Not proved:** that this is *wrong*. The internet legitimately answers external-world
  questions the user cannot; a household assistant that looks things up should hold web
  facts. The problem is not that they exist — it is that they **outnumber the user's own
  by 2.3× and are the only labelled source**.
- **Not measured:** whether the ratio is *moving*. This is a snapshot; without a trend, a
  decision to "fix" it is a guess about direction.

## Recommendation

Do not "reduce search" — the internet is a legitimate source. Do these instead, cheapest
first:

1. **Label the conversational path.** One `source_type="user"` at
   `orchestrator.py:2167` turns the 22% "unknown" bucket into the user's share and makes
   every future measurement trivial. It is a one-line change with no behavioural effect.
2. **Decide the intended ratio, then measure it.** "The user should be at least half of
   what I know about my world" is a testable target; today there is none, so the drift
   cannot be called drift.
3. **Give the 275 conflicts a consumer** — a correction backlog nobody reaches is not a
   correction mechanism.
