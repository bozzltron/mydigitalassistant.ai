#!/usr/bin/env python3
"""Close the alerts raised by the removed correction/search writers.

Run inside the backend container (it needs the live brain):

    docker exec assistant-backend python -m assistant.scripts.close_noise_alerts
    docker exec assistant-backend-dev python -m assistant.scripts.close_noise_alerts --dry-run

Why this exists
---------------
Two writers raised an alert on every correction, and one raised one for facts a
search had just stored. All three fired *while the user was in the conversation*
and announced something already on screen, violating the presence rule:

    An alert is warranted when the agent learned something and the user was
    not there to hear it.

The writers are gone, but the rows they produced remain open. They cannot be
resolved by replying, because there is nothing to reply to — a notice about
something that already happened. So they are closed here, once.

Scope: only the alert `kind`s named below. A blanket read-all would also close
the genuine task alerts the user still needs to see. Idempotent: resolved rows
are not in the open set, so a second run reports 0.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from assistant.backend.config import settings
from assistant.backend.memory.store import MemoryStore

# Alert kinds raised by writers that have since been removed. `correction` and
# `search_result` announced work done in the conversation; `task_alert` and
# `task_failure` are legitimate and are deliberately not listed.
REMOVED_WRITER_KINDS = {"correction", "search_result"}


async def _run(user_id: int, dry_run: bool) -> int:
    store = MemoryStore(settings.database_path)
    open_alerts = await store.get_alerts(user_id, unread_only=True, limit=1000)
    noise = [a for a in open_alerts if a.type in REMOVED_WRITER_KINDS]

    print(f"open alerts: {len(open_alerts)}")
    for a in open_alerts:
        mark = "  (noise)" if a.type in REMOVED_WRITER_KINDS else ""
        print(f"  id={a.id} kind={a.type} severity={a.severity} {a.title[:50]!r}{mark}")

    if not noise:
        print("\nnothing to close.")
        return 0

    if dry_run:
        print(f"\n[dry-run] would close {len(noise)} alert(s): {REMOVED_WRITER_KINDS}")
        return 0

    closed = await store.resolve_alerts_by_type(user_id, REMOVED_WRITER_KINDS)
    print(f"\nclosed {closed} alert(s) of kind {sorted(REMOVED_WRITER_KINDS)}.")
    remaining = await store.get_unread_alert_count(user_id)
    print(f"open alerts remaining: {remaining}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would close, change nothing"
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.user_id, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
