"""One-off recovery: re-create the alerts lost by a migration that ran too early.

`migrate_alerts_to_frames.py` was run against the **old image**, so `create_alert`
still wrote to the `alerts` table. It reported "migrated 8", wrote 8 table rows, and
then dropped the table — losing them. The rows survive in the pre-migration backup
(`backup-20260930-201933.db`).

The lesson is recorded rather than hidden: a migration must be run by the code it
migrates *to*, and the dry run said nothing because the dry run was correct — the
defect was that the running process did not have the new storage path.

This reads the 8 rows from the backup and creates them as alert frames with the new
code. Run with `--apply`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.db.sqlcipher import connect
from assistant.backend.memory.store import MemoryStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# Rows that express something the agent wanted to tell an absent user.
MIGRATE = ("correction", "task_alert")
DEFAULT_BACKUP = "/app/data/backup-20260930-201933.db"


async def main(backup: str, apply: bool) -> int:
    if not Path(backup).exists():
        print(f"backup not found: {backup}")
        return 1

    conn = connect(backup)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, user_id, type, title, message, severity, is_read, created_at "
        f"FROM alerts WHERE type IN ({','.join('?' * len(MIGRATE))}) ORDER BY id",
        MIGRATE,
    ).fetchall()

    print(f"recoverable alerts in {backup}: {len(rows)}")
    for r in rows:
        print(f"  [{r[2]}/{r[5]}] {r[3][:70]}")

    if not apply:
        print("\ndry run -- nothing written. Re-run with --apply")
        return 0

    store = MemoryStore(settings.database_path)
    created = 0
    for _id, user_id, type_, title, message, severity, is_read, _created in rows:
        alert = await store.create_alert(
            user_id=user_id,
            type=type_,
            title=title,
            message=message,
            severity=severity or "info",
        )
        if is_read:
            await store.resolve_alert(alert.id)
        created += 1

    # Verify against the live brain, not against the return value.
    live = await store.get_alerts(1, unread_only=True, limit=1000)
    print(f"\ncreated {created}")
    print(f"open alerts now in the live brain: {len(live)}")
    for a in live:
        print(f"  [{a.type}/{a.severity}] {a.title[:70]}")
    await store.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", default=DEFAULT_BACKUP)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.backup, args.apply)))
