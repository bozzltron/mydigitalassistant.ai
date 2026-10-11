# Result — should the `needs_action` nudge fire on read-class requests?

**No — scope it to state changes.** The change-scoped question holds `change` recall
at **10/10** while firing on **0/15** read/answer cases; the current question fires on
**4/15** under the deployed model. Target accuracy **1.000 vs 0.750**, at 78 ms vs
72 ms. The narrowing costs nothing and removes the whole read band.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — change recall ≥ 90%** | **holds** | 10/10 = 100% on all three models |
| **H2 — fires on 0 read/answer** | **holds** | 0/15 |
| **H3 — the current question fires on ≥ 1 read/answer** | **holds** | 4/15 under `tev1:0.8b` |
| **H4 — the union keeps change ≥ 90%, search 3/3, read/answer 0/15** | **holds for `tev1:0.8b`** | 10/10, 3/3, 0/15, target acc 1.000 |

## Falsification conditions, resolved

1. change recall < 80% → not triggered (100%).
2. recall ≥ 90% **and** 0 read/answer fired **and** the current question fires on ≥ 1
   → **triggered** for the change question.
3. the current question fires on 0 read/answer → not triggered (it fires on 4).
4. union change recall < 90% **or** ≥ 1 read/answer fired → not triggered for
   `tev1:0.8b` (10/10, 0/15, search 3/3) → **ship the union wording**.

## The finding

- **The split is free.** Every write-class request is still caught (10/10, unchanged);
  every read-class request stops firing (0/15). There is no accuracy to trade away.
- **Firing on reads was deliberate, not accidental.** The routing probe biased the
  threshold to 0.4 to recover one false-negative read; that same bias pulls in three
  more (0.425–0.490). Scoping to changes removes the band, so the threshold no longer
  has to be tuned against reads at all.
- **The spurious fires are all `read`** — files, task lists, contact lists: exactly the
  requests memory can answer. No `answer` case fires, so the annoyance is narrow and
  specific, not diffuse.
- **Change-only also drops search** (3/3 → 0/3) — and that turned out **not** to be
  safe. The router forces search only when `plan.sufficiency == MemorySufficiency.NONE`
  (`orchestrator.py:1450–1466`), so a lookup with a relevant-but-stale memory fact is
  not forced. The **union** wording restores it: `tev1:0.8b` fires 3/3 on search and
  stays 0/15 on read/answer.

## What this proves, and what it does not

- **Proved:** a change-scoped question classifies the write-class set as well as the
  current one (perfectly, here) and is silent on read-class requests. The design
  principle's asymmetry holds in the measurement: the guard's value is on state
  changes.
- **Not proved:** that the model then behaves correctly on a read-class turn. That is
  verified on the live scenario — a read request ("list my open tasks") must answer
  **without** an `acting` round, and the write case must still write.
- **Not proved, and resolved by design:** the router's search coverage. It was checked
  and is partial (`sufficiency == NONE` only), so search belongs **in** the nudge's
  scope — hence the union. What remains behavioural is that a nudged lookup actually
  searches, verified on the live scenario.
- **Not proved:** that the model behaves correctly on a read-class turn. Verified on the
  live scenario — a read request ("list my open tasks") must answer **without** an
  `acting` round.

## Recommendation

Scope `needs_action` to **state changes + external lookups** — the deeds whose result
words can counterfeit:

- **Question:** "does this request ask the assistant to perform an action that **changes
  something** (create, edit, rename, delete, append, move) **or fetches current external
  information** (search the web) — as opposed to answering from what it already knows
  (including reading its own stored memory)?"
- **Keep** the model (`tev1:0.8b`) and the action-biased threshold (0.4).
- **Verify** on the live scenario: the write case still writes, a lookup still searches,
  and a read case answers without a spurious `acting` round.

This is the pre-registered outcome for conditions 2 and 4. The change is one question
and its two criteria in `pipeline/decision.py`.
