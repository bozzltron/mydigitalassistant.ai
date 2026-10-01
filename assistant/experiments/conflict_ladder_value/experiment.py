"""Is the conflict ladder doing anything, or is it decoration around "new wins"?

`resolve_conflict` implements `source_reliability -> confidence -> priority ->
recency`. Live data says the first three rungs never fire:

    auto_resolved conflicts:              2,381
      where resolved_value == new_value:  2,381   (100%)
      where existing won:                     0

The hypothesis: extraction writes every slot with `source_reliability=null` -> 0.5, and
`revise()` passes `new_source_reliability=None` -> also 0.5, so rung one never
discriminates and recency decides everything.

This measures whether the ladder changes any decision when the provenance it was
designed to consume is actually supplied. See plan.md for the hypotheses, the bars
fixed in advance, and the threats.

Read-only with respect to the live brain: runs against a copy, gated by preflight.py.

Usage:
    EXP_DB=/exp/assistant.db python -m assistant.experiments.conflict_ladder_value.experiment
Env:
    EXP_DB     path to the brain copy (required)
    CLV_OUT    result json path (default ./result.json)
    CLV_SAMPLE max conflicts to sample (default 0 = all)
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from assistant.backend.db.sqlcipher import aiosqlite_connect
from assistant.backend.memory.confidence import resolve_conflict
from assistant.backend.pipeline.extractor import IDENTITY_FRAME

DB = os.environ.get("EXP_DB", "")
OUT = Path(os.environ.get("CLV_OUT", "result.json"))
SAMPLE = int(os.environ.get("CLV_SAMPLE", "0"))

# Kept out of the sample: the summarizer's self-inflicted rows, which measure the
# summarizer rather than the ladder (plan threat 2).
EXCLUDED_TYPES = ("conversation_summary",)

# A slot nobody should silently overwrite. Used for H4.
PROTECTED_KEYS = ("full_name",)


@dataclass
class Case:
    """One historical conflict, with both sides' real provenance."""

    conflict_id: int
    frame_id: int
    frame_type: str
    slot_key: str
    existing_value: str
    new_value: str
    existing_reliability: float | None
    new_reliability: float | None
    existing_confidence: float
    existing_priority: float
    # Which side production actually chose, from the recorded row.
    production_winner: str  # "new" | "existing" | "unknown"
    protected: bool


async def load_cases(db_path: str) -> tuple[list[Case], dict]:
    """Every historical conflict that can be reconstructed, plus exclusion counts."""
    excluded: dict[str, int] = {}
    cases: list[Case] = []

    async with aiosqlite_connect(db_path) as db:
        rows = await db.execute_fetchall(
            """
            SELECT c.id, c.frame_id, f.type, c.slot_key,
                   c.existing_value, c.new_value, c.resolved_value, c.status,
                   s.source_reliability, s.confidence, s.priority, f.name
            FROM conflicts c
            JOIN frames f ON f.id = c.frame_id
            LEFT JOIN slots s ON s.frame_id = c.frame_id AND s.key = c.slot_key
            WHERE c.existing_value IS NOT NULL
              AND c.new_value IS NOT NULL
              AND c.existing_value != c.new_value
            ORDER BY c.id
            """
        )

    for (
        cid, fid, ftype, key, old, new, resolved, status,
        rel, conf, prio, fname,
    ) in rows:
        if ftype in EXCLUDED_TYPES:
            excluded[ftype] = excluded.get(ftype, 0) + 1
            continue

        if status == "auto_resolved" and resolved == new:
            winner = "new"
        elif status == "auto_resolved" and resolved == old:
            winner = "existing"
        else:
            winner = "unknown"

        # A conflict on the agent's own name, or on a slot whose frame is the
        # identity frame, is the `grok` exposure.
        protected = fname == IDENTITY_FRAME or key in PROTECTED_KEYS

        cases.append(
            Case(
                conflict_id=cid,
                frame_id=fid,
                frame_type=ftype,
                slot_key=key,
                existing_value=old,
                new_value=new,
                existing_reliability=rel,
                new_reliability=None,  # what production supplied (the hypothesis)
                existing_confidence=conf if conf is not None else 0.5,
                existing_priority=prio if prio is not None else 0.5,
                production_winner=winner,
                protected=protected,
            )
        )

    return cases, excluded


def decide(
    case: Case,
    *,
    fed: bool,
) -> str:
    """Run one case through resolve_conflict and report which side won.

    Condition A (`fed=False`) supplies exactly what production does:
    `new_source_reliability=None`. Condition B (`fed=True`) supplies each side's real
    provenance from the slot row.

    The "new" side's reliability is taken from the same slot in condition B, because
    that is the row production wrote — the slot now holds the new value, so its
    recorded provenance is the new side's provenance.
    """
    new_rel = case.existing_reliability if fed else None
    decision = resolve_conflict(
        existing_value=case.existing_value,
        new_value=case.new_value,
        existing_confidence=case.existing_confidence,
        new_confidence=0.5,  # revise() passes initial_confidence()
        existing_source_reliability=case.existing_reliability,
        new_source_reliability=new_rel,
        existing_priority=case.existing_priority,
        new_priority=0.5,
    )
    if decision.winning_value == case.new_value:
        return "new"
    if decision.winning_value == case.existing_value:
        return "existing"
    return "other"


async def main() -> int:
    if not DB:
        print("EXP_DB is required (path to a brain copy)")
        return 2
    if not Path(DB).exists():
        print(f"EXP_DB does not exist: {DB}")
        return 2

    cases, excluded = await load_cases(DB)
    total = len(cases)

    if SAMPLE and SAMPLE < total:
        random.seed(20260930)  # recorded; deterministic
        cases = random.sample(cases, SAMPLE)

    print(f"brain copy : {DB}")
    print(f"conflicts  : {total} reconstructable" + (f", sampled {len(cases)}"
                                                     if len(cases) != total else ""))
    print(f"excluded   : {excluded}")
    print()

    # --- condition A: what production supplied ---
    a = [decide(c, fed=False) for c in cases]
    # --- condition B: honest provenance ---
    b = [decide(c, fed=True) for c in cases]

    disagree = sum(1 for x, y in zip(a, b, strict=True) if x != y)
    a_new = sum(1 for x in a if x == "new")
    b_new = sum(1 for x in b if x == "new")
    b_existing = sum(1 for x in b if x == "existing")

    # H4: how many historical conflicts sat on a protected slot at all, and how
    # many of those would the fed ladder have given to the new (possibly hostile)
    # side.
    protected_cases = [c for c in cases if c.protected]
    protected_new_wins_b = sum(
        1 for c in protected_cases if decide(c, fed=True) == "new"
    )

    # Agreement between production's recorded outcome and condition A, which is a
    # check that A reproduces production rather than an independent finding.
    reproduced = sum(
        1 for c, w in zip(cases, a, strict=True)
        if c.production_winner == "unknown" or c.production_winner == w
    )
    production_checkable = sum(1 for c in cases if c.production_winner != "unknown")

    report = {
        "db": DB,
        "sample_size": len(cases),
        "total_reconstructable": total,
        "excluded": excluded,
        "condition_a_new_wins": a_new,
        "condition_a_existing_wins": sum(1 for x in a if x == "existing"),
        "condition_b_new_wins": b_new,
        "condition_b_existing_wins": b_existing,
        "disagreements": disagree,
        "disagreement_rate": round(disagree / len(cases), 4) if cases else 0.0,
        "rung1_fires_in_b": b_existing > 0,
        "production_reproduced_by_a": reproduced,
        "production_checkable": production_checkable,
        "protected_cases": len(protected_cases),
        "protected_new_wins_under_b": protected_new_wins_b,
        "cases": [asdict(c) for c in cases],
        "decisions_a": a,
        "decisions_b": b,
    }
    OUT.write_text(json.dumps(report, indent=2))

    a_existing = sum(1 for x in a if x == "existing")
    print(f"condition A (production inputs) : new={a_new} existing={a_existing}")
    print(f"condition B (fed provenance)    : new={b_new} existing={b_existing}")
    rate = report["disagreement_rate"]
    print(f"disagreements                   : {disagree}/{len(cases)} = {rate:.1%}")
    print()
    print(f"A reproduces production         : {reproduced}/{production_checkable} checkable")
    print(f"protected slots involved        : {len(protected_cases)}")
    print(f"  of which B gives to new       : {protected_new_wins_b}")
    print()
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
