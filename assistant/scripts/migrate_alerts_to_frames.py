"""One-off: migrate the `alerts` table into frames, then drop the table.

Phase C.1. An alert is memory of a type, so the table was a second storage
mechanism for something the memory model already expresses.

Migration policy, in order:

- **`task_result` rows are dropped, not migrated.** "Task completed:
  job_postings_monitor" is not something the agent wants to tell the user -- it is
  mechanism. Carrying 54 of them into memory as frames would import the noise this
  plan exists to remove.
- **`search_result` rows are dropped.** They fired mid-conversation, when the user
  was watching the agent learn. The presence rule says those are not alerts.
- **`conflict` rows are dropped.** They were auto-resolved conflicts announced in
  the conversation the user was already in. Same reasoning.
- **`correction` and `task_alert` rows are migrated.** These are the agent telling
  the user something they were not there for: a correction that was contradicted,
  and a task that found something worth flagging.

Resolved vs new: every migrated row is created as `new` unless it was already
`is_read`, in which case it is created and immediately resolved -- so the bell does
not resurrect history the user already saw.

Run with `--apply`. Without it, prints what it would do and writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from assistant.backend.config import settings
from assistant.backend.db.sqlcipher import connect
from assistant.backend.memory.store import MemoryStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_alerts")

# Rows that express something the agent wanted to tell an absent user.
MIGRATE = ("correction", "task_alert", "important")
# Rows that are mechanism or in-conversation noise. Dropped, not carried forward.
DROP = ("task_result", "search_result", "conflict")


async def main(apply: bool) -> int:
    conn = connect(settings.database_path)
    cur = conn.cursor()

    try:
        rows = cur.execute(
            "SELECT id, user_id, type, title, message, severity, is_read, created_at "
            "FROM alerts ORDER BY id"
        ).fetchall()
    except Exception as exc:
        print(f"no alerts table (already migrated?): {exc}")
        return 0

    total = len(rows)
    migrate_rows = [r for r in rows if r[2] in MIGRATE and not r[6]]
    already_read = [r for r in rows if r[2] in MIGRATE and r[6]]
    drop_rows = [r for r in rows if r[2] in DROP or r[2] not in MIGRATE]

    print(f"alerts table: {total} rows")
    print(f"  migrate as open     : {len(migrate_rows)}")
    print(f"  migrate as resolved : {len(already_read)}  (already read)")
    print(f"  drop                : {len(drop_rows)}")
    by_type: dict[str, int] = {}
    for r in drop_rows:
        by_type[r[2]] = by_type.get(r[2], 0) + 1
    for k, v in sorted(by_type.items()):
        print(f"      {k}: {v}")

    if not apply:
        print("\ndry run -- nothing written. Re-run with --apply")
        return 0

    store = MemoryStore(settings.database_path)
    migrated = 0
    resolved = 0
    for _id, user_id, type_, title, message, severity, is_read, _created in rows:
        if type_ not in MIGRATE:
            continue
        alert = await store.create_alert(
            user_id=user_id,
            type=type_,
            title=title,
            message=message,
            severity=severity or "info",
        )
        migrated += 1
        if is_read:
            await store.resolve_alert(alert.id)
            resolved += 1

    # Drop the table. Backing up first is the caller's responsibility, per the
    # precedent set by the consolidation merge path.
    cur.execute("DROP TABLE IF EXISTS alerts")
    conn.commit()

    print(f"\nmigrated {migrated} ({resolved} created resolved)")
    print("dropped the alerts table")
    print(f"integrity: {cur.execute('PRAGMA integrity_check').fetchone()[0]}")
    await store.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the migration")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.apply)))
