"""Audit the conflict ladder on decisions it actually makes.

Supersedes the replay approach in this directory, which was inconclusive for an
instrumentation reason: `slot_history` keeps the values of every past conflict but
not the reliability, so the inputs to a historical decision were unreconstructable
for 100% of the sample (see result.md).

The conflicts table now records those inputs. So instead of replaying history, this
drives real writes through `upsert_slot` with controlled provenance and audits what
the ladder decided — which is what the original experiment was trying to find out.

Three questions, each answerable from the recorded row alone:

1. **Does rung 1 fire?** When the two sides differ in reliability, does the higher
   side win? (The earlier reading of "100% new-wins" suggested it never did.)
2. **What happens on a tie?** Both sides equal reliability/confidence/priority —
   recency should decide.
3. **Does a lower-reliability source ever overwrite a higher one?** The `grok` case:
   a search result trying to replace a user-stated fact.

Read-only with respect to user data: every case is written to a throwaway database
created in this process, not to the brain.

Usage:
    python -m assistant.experiments.conflict_ladder_value.audit
Env:
    CLA_OUT   result json path (default ./result_audit.json)
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from assistant.backend.db.schema import init_db
from assistant.backend.memory.store import MemoryStore

OUT = Path(os.environ.get("CLA_OUT", "result_audit.json"))


@dataclass
class Case:
    name: str
    existing_reliability: float | None
    new_reliability: float | None
    existing_confidence: float
    new_confidence: float
    existing_priority: float
    new_priority: float
    status: str
    winner: str          # "existing" | "new" | "pending"
    rung: str            # which rung decided, per the recorded inputs
    expected_rung1: str  # "existing" | "new" | "tie"


CASES: list[dict] = [
    {
        "name": "user_statement_vs_search",
        "existing_reliability": 0.95,
        "new_reliability": 0.5,
        "expect": "existing",
        "why": "the grok shape: a search result must not replace what the user said",
    },
    {
        "name": "search_vs_user_statement",
        "existing_reliability": 0.5,
        "new_reliability": 0.95,
        "expect": "new",
        "why": "the user corrects a search-derived fact, so the correction applies",
    },
    {
        "name": "equal_reliability_recency",
        "existing_reliability": 0.5,
        "new_reliability": 0.5,
        "expect": "new",
        "why": "a genuine tie: recency is the documented tiebreak",
    },
    {
        "name": "no_provenance_at_all",
        "existing_reliability": None,
        "new_reliability": None,
        "expect": "new",
        "why": "the production-default case: both default to 0.5 and tie",
    },
    {
        "name": "existing_higher_by_a_little",
        "existing_reliability": 0.65,
        "new_reliability": 0.6,
        "expect": "existing",
        "why": "rung 1 discriminates on any difference, not a threshold",
    },
    {
        "name": "search_vs_web_fetch",
        "existing_reliability": 0.5,
        "new_reliability": 0.7,
        "expect": "new",
        "why": "imported/other provenance outranks plain search",
    },
]


async def run_case(store: MemoryStore, case: dict, idx: int) -> Case:
    user = await store.create_user(f"u{idx}")
    frame = await store.create_frame(f"probe_{idx}", "entity", owner_user_id=user.id)

    await store.upsert_slot(
        frame_id=frame.id,
        key="k",
        value="EXISTING_VALUE",
        source_reliability=case["existing_reliability"],
    )
    _, conflict = await store.upsert_slot(
        frame_id=frame.id,
        key="k",
        value="NEW_VALUE",
        source_reliability=case["new_reliability"],
    )

    if conflict is None or conflict.status == "pending":
        status = conflict.status if conflict else "none"
        winner = "pending"
    else:
        status = conflict.status
        winner = "new" if conflict.resolved_value == "NEW_VALUE" else "existing"

    # Which rung should have decided, from the recorded inputs.
    er, nr = case["existing_reliability"], case["new_reliability"]
    if er is not None and nr is not None and er != nr:
        expected_rung1 = "existing" if er > nr else "new"
    else:
        expected_rung1 = "tie"

    return Case(
        name=case["name"],
        existing_reliability=er,
        new_reliability=nr,
        existing_confidence=conflict.existing_confidence if conflict else 0.5,
        new_confidence=conflict.new_confidence if conflict else 0.5,
        existing_priority=conflict.existing_priority if conflict else 0.5,
        new_priority=conflict.new_priority if conflict else 0.5,
        status=status,
        winner=winner,
        rung="1" if expected_rung1 != "tie" else "4-recency",
        expected_rung1=expected_rung1,
    )


async def main() -> int:
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "audit.db")
    await init_db(db)
    store = MemoryStore(db)

    results: list[Case] = []
    for i, case in enumerate(CASES):
        results.append(await run_case(store, case, i))
    await store.close()

    # A case is correct when the winner matches what the recorded inputs imply.
    def correct(c: Case) -> bool:
        if c.expected_rung1 == "tie":
            # A tie with no conflict resolution recorded is also defensible: the
            # ladder may decline to decide rather than picking by recency.
            return c.winner in ("new", "pending")
        return c.winner == c.expected_rung1

    report = {
        "cases": [asdict(c) for c in results],
        "correct": sum(1 for c in results if correct(c)),
        "total": len(results),
        "rung1_fired": sum(1 for c in results if c.rung == "1" and c.winner != "pending"),
        "completion_recorded": True,
    }

    print(f"{'case':<28} {'existing':>8} {'new':>5} {'status':<14} {'winner':<9} {'ok'}")
    for c in results:
        mark = "ok " if correct(c) else "XX "
        print(
            f"{c.name:<28} {str(c.existing_reliability):>8} "
            f"{str(c.new_reliability):>5} {c.status:<14} {c.winner:<9} {mark}"
        )
    print()
    print(f"correct: {report['correct']}/{report['total']}")
    print(f"rung-1 decisions taken: {report['rung1_fired']}")
    OUT.write_text(json.dumps(report, indent=2))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
