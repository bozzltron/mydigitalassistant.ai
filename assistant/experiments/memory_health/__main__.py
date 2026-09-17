#!/usr/bin/env python3
"""Memory Health Check - Atomic Experiment.

Single file, runs in <10s, exits 1 if unhealthy.
Usage: python -m assistant.experiments.memory_health /path/to/assistant.db
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from assistant.backend.db.sqlcipher import aiosqlite_connect

HEALTHY_THRESHOLDS = {
    "conflict_rate_pct": 5.0,
    "high_conf_pct": 30.0,
    "reinforced_pct": 20.0,
    "connected_frames_pct": 60.0,
    "essential_deleted": 0,
    "pending_conflicts": 0,
}


async def check_health(db_path: str) -> dict:
    async with aiosqlite_connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout = 15000")

        # Total slots
        total_slots = (await db.execute_fetchall("SELECT COUNT(*) FROM slots"))[0][0] or 1

        # Conflicts
        conflict_count = (await db.execute_fetchall("SELECT COUNT(*) FROM conflicts"))[0][0]
        conflict_rate = conflict_count / total_slots * 100

        pending_conflicts = (await db.execute_fetchall(
            "SELECT COUNT(*) FROM conflicts WHERE status = 'pending'"
        ))[0][0]

        # High confidence slots
        high_conf = (await db.execute_fetchall(
            "SELECT COUNT(*) FROM slots WHERE confidence > 0.7"
        ))[0][0]
        high_conf_pct = high_conf / total_slots * 100

        # Reinforced slots (EXPAND in history)
        reinforced = (await db.execute_fetchall(
            "SELECT COUNT(DISTINCT slot_id) FROM slot_history WHERE reason = 'EXPAND'"
        ))[0][0]
        reinforced_pct = reinforced / total_slots * 100

        # Frame connectivity
        total_frames = (await db.execute_fetchall(
            "SELECT COUNT(*) FROM frames WHERE deleted_at IS NULL"
        ))[0][0] or 1
        connected = (await db.execute_fetchall("""
            SELECT COUNT(DISTINCT f.id) FROM frames f
            JOIN associations a ON f.id = a.from_frame_id OR f.id = a.to_frame_id
            WHERE f.deleted_at IS NULL
        """))[0][0]
        connected_pct = connected / total_frames * 100

        # Essential frames deleted
        essential_deleted = (await db.execute_fetchall("""
            SELECT COUNT(*) FROM frames WHERE essential = 1 AND deleted_at IS NOT NULL
        """))[0][0]

        return {
            "conflict_rate_pct": round(conflict_rate, 2),
            "high_conf_pct": round(high_conf_pct, 1),
            "reinforced_pct": round(reinforced_pct, 1),
            "connected_frames_pct": round(connected_pct, 1),
            "essential_deleted": essential_deleted,
            "pending_conflicts": pending_conflicts,
        }


def evaluate(metrics: dict) -> tuple[bool, list[str]]:
    """Return (healthy, violations)."""
    violations = []
    for metric, threshold in HEALTHY_THRESHOLDS.items():
        value = metrics.get(metric, 0)
        if metric in ("essential_deleted", "pending_conflicts"):
            if value > threshold:
                violations.append(f"{metric}: {value} (max {threshold})")
        else:
            if value < threshold:
                violations.append(f"{metric}: {value:.1f}% (min {threshold}%)")
    return len(violations) == 0, violations


async def main(db_path: str) -> int:
    if not Path(db_path).exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    metrics = await check_health(db_path)
    healthy, violations = evaluate(metrics)

    result = {
        "timestamp": datetime.now(UTC).isoformat(),
        "db_path": db_path,
        "healthy": healthy,
        "metrics": metrics,
        "violations": violations,
    }

    # Save to cwd
    out_path = Path.cwd() / f"memory_health_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(result, indent=2))

    # Print summary
    print(json.dumps(result, indent=2))

    if not healthy:
        print("\n❌ UNHEALTHY:", ", ".join(violations), file=sys.stderr)
        return 1

    print("\n✅ HEALTHY")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m assistant.experiments.memory_health /path/to/assistant.db", file=sys.stderr)
        sys.exit(2)

    import asyncio
    sys.exit(asyncio.run(main(sys.argv[1])))