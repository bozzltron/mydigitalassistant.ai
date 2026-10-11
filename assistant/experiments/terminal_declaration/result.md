# Result — does an explicit terminal declaration beat the `needs_action` judgement?

**No. Retire it.** The structural declaration is *correct* — the model complies, and
completion is unchanged — but it is **not cheaper**, and a contract that forbids the
re-check made it *worse*, not better. The cost is inherent to naming an explicit end
state, so the judgement stays.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — compliance ≥ 90%** | **holds** | F 4/4, F2 5/5 answer turns called `finalize` (C: 0/5) |
| **H2 — completion ≥ judged** | **holds** | change 4/4 = 4/4; announce 2/2 = 2/2, all three policies |
| **H3 — rounds ≤ judged + 0.5** | **fails** | F 2.60 vs C 2.00; action turns median 4 vs 2.5 |
| **H4 — structural catch, no judgement** | **holds** | deterministic: 2 rounds, finalized, judgement consulted 0 times |
| **H5 — the tightening closes the gap** | **fails** | F2 3.18 vs F 2.60; action turns median 4 → 5 |

## Falsification conditions, resolved

1. H1 fails → not triggered.
2. H1 holds but H2 fails → not triggered (completion equal).
3. H1, H2 **and** H3 hold → **not met**: H3 fails.
4. F2 compliance < 90% or completion < C → not triggered (5/5, 4/4, 2/2).
5. F2 rounds still > C + 0.5 → **triggered** → **retire the structural idea.**

## The finding

- **The idea is sound and the model cooperates.** Removing the ambiguity — "no tool call"
  could be an answer or an unfinished action — genuinely works: told to end by declaring,
  the model declares, and it does not skip actions to get there.
- **But an explicit end state costs work.** The first run suggested the model verifies
  before declaring (`write_file, read_file, finalize`). The addendum tested that directly
  by forbidding the re-check — and the rounds went **up**, not down. So the cost is not
  the verification pass; it is that **making the turn's end explicit makes the model do
  more tool work to justify the declaration.** The mechanism is behavioural, not
  accounting, and it did not respond to instruction.
- **Twice now, machinery around the model has failed to pay.** The shared-nudge-bound
  probe was negative (the case never occurred); this one is negative on cost. The
  architecture is telling us that **guards are not free and the model does not become
  cheaper by being watched** — which is the frame any further redundancy should be
  judged in.

## What this proves, and what it does not

- **Proved:** the structural declaration is a valid alternative to the judgement —
  correct, compliant, and judgement-free — but it costs ~1–2 rounds per action turn and
  that cost is not removable by contract wording.
- **Not proved:** that the *fleet* saving (retiring a model, a scope, a threshold, a
  shared bound) is smaller than the latency cost. The probe priced latency only. On the
  measured axis, though, the judgement wins, and a second attempt to flip it failed.

## Recommendation

**Keep the judgement; retire the structural idea.** Per falsification condition 5, the
extra rounds are inherent, so there is nothing to gain by re-contracting.

- The `require_terminal` path was **removed** from `stream_tool_loop`, the contract
  variants from `llm_client`, and the terminal regression test — they existed only for
  this experiment, and the experiment is closed. `experiment.py` is kept as the record of
  what was run; re-running it needs that diff, which is in this commit history.
- The durable lesson is the one to carry forward: **redundancy and guarding have a
  measured cost, and two independent attempts to buy reliability cheaper than the
  current judgement both failed.** Any "council of models" should be pre-registered
  against a ground truth and judged on accuracy-per-cost, not adopted for its
  resemblance to fault-tolerant hardware.
