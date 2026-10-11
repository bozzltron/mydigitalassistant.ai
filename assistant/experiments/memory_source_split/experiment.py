"""Is the brain a model of the user, or a cache of the internet? See plan.md.

Read-only: groups live frames and slots by ``source_type`` and buckets them into
internet / user / derived. Writes nothing to the brain.

    docker cp assistant/experiments/memory_source_split/experiment.py assistant-backend:/tmp/mss.py
    docker exec assistant-backend python /tmp/mss.py
"""

from __future__ import annotations

import asyncio
import json

from assistant.backend.config import settings
from assistant.backend.memory.store import MemoryStore

# Fixed in plan.md, before the data.
INTERNET = {"search", "web_fetch"}
USER = {
    None,  # the conversational path passes no source_type (orchestrator.py:2167)
    "user",
    "user_correction",
    "user_supplied",
    "conversation",
    "correction",
    "file_upload",
}


def bucket(source_type: str | None) -> str:
    if source_type in INTERNET:
        return "internet"
    if source_type in USER:
        return "user"
    return "derived"


async def main() -> None:
    store = MemoryStore(settings.database_path)
    async with store._connect() as db:

        async def rows(sql: str):
            return await db.execute_fetchall(sql)

        out: dict = {"db": settings.database_path}

        for table, where in (("frames", "WHERE deleted_at IS NULL"), ("slots", "")):
            r = await rows(f"SELECT source_type, COUNT(*) FROM {table} {where} GROUP BY 1")
            raw = {(k if k is not None else "NULL"): n for k, n in r}
            counts = {"internet": 0, "user": 0, "derived": 0}
            for source_type, n in r:
                counts[bucket(source_type)] += n
            total = sum(counts.values()) or 1
            out[table] = {
                "total": total,
                "bucket": counts,
                "share": {k: round(v / total, 3) for k, v in counts.items()},
                "raw": dict(sorted(raw.items(), key=lambda kv: -kv[1])),
            }

        out["null_sample"] = [
            r[0]
            for r in await rows(
                "SELECT name FROM frames WHERE source_type IS NULL "
                "AND deleted_at IS NULL LIMIT 12"
            )
        ]
        out["pending_conflicts"] = (
            await rows("SELECT COUNT(*) FROM conflicts WHERE status = 'pending'")
        )[0][0]
        out["frames_live"] = (
            await rows("SELECT COUNT(*) FROM frames WHERE deleted_at IS NULL")
        )[0][0]

        print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
