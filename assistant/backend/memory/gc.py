"""Priority decay garbage collection for memory slots.

Low-priority facts that are never reinforced should decay toward forgotten.
This module implements the GC logic but does NOT run automatically — it is
invoked on-demand via `assistant db gc`.

Decay rules:
- Essential slots (essential=1) are exempt.
- Slots with priority >= 0.5 are exempt.
- Slots with priority = 0 (already soft-deleted) are skipped.
- After DECAY_AFTER_DAYS (default 30) days with no reinforcement and
  priority < 0.5, decay begins: priority *= DECAY_RATE (0.95) per week.
- Slots whose priority drops below SOFT_DELETE_THRESHOLD (0.1) are soft-deleted.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

DECAY_AFTER_DAYS = 30
DECAY_RATE = 0.95
SOFT_DELETE_THRESHOLD = 0.1


@dataclass
class GcReport:
    scanned: int
    decayed: int
    soft_deleted: int
    errors: int


def _days_since_last_strengthened(last_strengthened_at: str | None) -> float | None:
    """Return number of days since last_strengthened_at (UTC), or None if absent."""
    if last_strengthened_at is None:
        return None
    try:
        normalized = last_strengthened_at.replace(" ", "T").replace("Z", "+00:00")
        last = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    now = datetime.now(UTC)
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    delta = now - last
    return delta.total_seconds() / (24 * 3600)


def compute_decayed_priority(priority: float, last_strengthened_at: str | None) -> float | None:
    """Compute decayed priority for a slot.

    Returns the new priority after applying weekly decay, or None if the slot
    is not eligible for decay (essential, priority >= 0.5, no last_strengthened_at,
    or within the decay-free window).

    Soft-deleted slots (priority == 0) are not decayed further.
    """
    if priority <= 0 or priority >= 0.5:
        return None

    days = _days_since_last_strengthened(last_strengthened_at)
    if days is None:
        return None

    if days <= DECAY_AFTER_DAYS:
        return None

    decay_weeks = (days - DECAY_AFTER_DAYS) / 7
    decay_factor = DECAY_RATE ** decay_weeks
    new_priority = priority * decay_factor

    if new_priority < SOFT_DELETE_THRESHOLD:
        return 0.0
    return new_priority


async def run_gc(db_path: str, dry_run: bool = False) -> GcReport:
    """Run garbage collection on all eligible slots.

    Args:
        db_path: Path to the SQLite database.
        dry_run: If True, compute decayed priorities but do not write them.

    Returns:
        GcReport with counts of scanned, decayed, soft-deleted, and errored slots.
    """
    from assistant.backend.db.sqlcipher import aiosqlite_connect

    report = GcReport(scanned=0, decayed=0, soft_deleted=0, errors=0)

    async with aiosqlite_connect(db_path) as db:
        rows = await db.execute_fetchall(
            """
            SELECT id, frame_id, key, priority, essential, last_strengthened_at
            FROM slots
            WHERE essential = 0 AND priority > 0 AND priority < 0.5
            """,
        )

    for row in rows:
        slot_id, frame_id, key, priority, essential, last_strengthened_at = row
        report.scanned += 1

        try:
            new_priority = compute_decayed_priority(priority, last_strengthened_at)
            if new_priority is None:
                continue

            if new_priority == 0:
                report.soft_deleted += 1
            else:
                report.decayed += 1

            if not dry_run:
                async with aiosqlite_connect(db_path) as db:
                    await db.execute(
                        "UPDATE slots SET priority = ?, updated_at = datetime('now') WHERE id = ?",
                        (new_priority, slot_id),
                    )
                    await db.commit()
        except Exception:
            report.errors += 1

    return report
