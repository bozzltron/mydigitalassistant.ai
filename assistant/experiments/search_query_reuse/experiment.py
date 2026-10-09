"""Do search queries repeat enough to justify a response cache?

Read-only against a brain copy. See ``plan.md`` for the design, the falsification
conditions, and the threats to validity.

    EXP_DB=/exp/assistant.db python assistant/experiments/search_query_reuse/experiment.py

Writes ``result.json``.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from assistant.backend.db.sqlcipher import aiosqlite_connect

DB_PATH = os.environ.get("EXP_DB", "/exp/assistant.db")
OUT_DIR = Path(__file__).parent
DIGEST_TABLES = ("frames", "slots", "associations", "episodes", "slot_history", "conflicts")

# Window -> timedelta (None = cache forever / no expiry).
TTLS: list[tuple[str, timedelta | None]] = [
    ("1h", timedelta(hours=1)),
    ("24h", timedelta(hours=24)),
    ("7d", timedelta(days=7)),
    ("forever", None),
]

# Requests per search, by backend: Brave makes web + images (2); SearXNG makes
# one call (its own proxy supplies images).
REQUESTS_PER_SEARCH = {"brave": 2, "searxng": 1}


def normalize(query: str) -> str:
    return " ".join(query.lower().split())


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def digest_db() -> dict:
    """Row counts per table, as a non-mutation check."""
    async with aiosqlite_connect(DB_PATH) as db:
        out: dict[str, object] = {}
        for table in DIGEST_TABLES:
            try:
                out[table] = (
                    await db.execute_fetchall(f"SELECT COUNT(*) FROM {table}")
                )[0][0]
            except Exception as exc:  # table absent in an older schema
                out[table] = f"n/a ({type(exc).__name__})"
        return out


async def load_rows() -> list[dict]:
    """Every search turn's query, in time order."""
    async with aiosqlite_connect(DB_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT timestamp, session_id, search_info FROM episodes "
            "WHERE search_info IS NOT NULL ORDER BY timestamp"
        )
    out: list[dict] = []
    for ts, session_id, raw in rows:
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        query = (payload or {}).get("query")
        if not query:
            continue
        out.append(
            {
                "ts": parse_ts(ts),
                "session": session_id,
                "scheduled": str(session_id).startswith("scheduled"),
                "query": query,
                "norm": normalize(query),
                "backend": (payload or {}).get("backend"),
            }
        )
    return out


def ttl_hits(rows: list[dict], window: timedelta | None) -> tuple[int, list[dict]]:
    """Occurrences that had an identical earlier query within ``window``."""
    last: dict[str, dict] = {}
    hits = 0
    pairs: list[dict] = []
    for row in rows:
        prev = last.get(row["norm"])
        hit = False
        if prev is not None:
            if window is None:
                hit = True
            elif row["ts"] is not None and prev["ts"] is not None:
                hit = row["ts"] - prev["ts"] <= window
        if hit:
            hits += 1
            pairs.append(
                {
                    "query": row["query"],
                    "same_session": row["session"] == prev["session"],
                    "backend": row["backend"],
                    "gap_s": (
                        (row["ts"] - prev["ts"]).total_seconds()
                        if row["ts"] and prev["ts"]
                        else None
                    ),
                }
            )
        last[row["norm"]] = row
    return hits, pairs


async def main() -> None:
    if not Path(DB_PATH).exists():
        raise SystemExit(f"brain not found at {DB_PATH}")

    before = await digest_db()
    print("db digest before:", json.dumps(before))

    rows = await load_rows()
    total = len(rows)
    if not total:
        raise SystemExit("no stored search queries — nothing to measure")

    unique = len({r["norm"] for r in rows})
    repeat_rate = 1 - unique / total

    backend_mix = Counter(r["backend"] or "?" for r in rows)

    print(f"\nsearch turns: {total}   unique queries: {unique}   repeat rate: {repeat_rate:.0%}")
    print(f"backend mix:  {dict(backend_mix)}")

    windows: dict[str, dict] = {}
    print("\nwindowed cache hits:")
    for label, window in TTLS:
        hits, pairs = ttl_hits(rows, window)
        hit_rate = hits / total
        same = sum(1 for p in pairs if p["same_session"])
        gaps = [p["gap_s"] for p in pairs if p["gap_s"] is not None]
        # Requests saved: a hit reuses the earlier turn's response.
        saved = sum(
            REQUESTS_PER_SEARCH.get(p["backend"] or "?", 1) for p in pairs
        )
        windows[label] = {
            "hits": hits,
            "hit_rate": round(hit_rate, 3),
            "same_session_hits": same,
            "cross_session_hits": hits - same,
            "median_gap_s": round(statistics.median(gaps), 1) if gaps else None,
            "requests_saved": saved,
        }
        print(
            f"  {label:>7}: hits={hits:<4} ({hit_rate:>5.0%})  "
            f"same-session={same:<3} cross-session={hits - same:<3}  "
            f"median gap={windows[label]['median_gap_s']}s"
        )

    # Split by session kind: a scheduled task re-issues its query every tick and
    # MUST see fresh results, so its repeats are not cacheable. The interactive
    # repeat rate is the class a cache can actually serve.
    breakdown: dict[str, dict] = {}
    print("\nby session kind:")
    for label, subset in (
        ("interactive", [r for r in rows if not r["scheduled"]]),
        ("scheduled", [r for r in rows if r["scheduled"]]),
    ):
        if not subset:
            continue
        u = len({r["norm"] for r in subset})
        rr = 1 - u / len(subset)
        h24, _ = ttl_hits(subset, timedelta(hours=24))
        breakdown[label] = {
            "turns": len(subset),
            "unique": u,
            "repeat_rate": round(rr, 3),
            "hits_24h": h24,
            "hit_rate_24h": round(h24 / len(subset), 3),
        }
        print(
            f"  {label:>11}: turns={len(subset):<4} unique={u:<4} "
            f"repeat={rr:>5.0%}  24h hits={h24} ({h24 / len(subset):.0%})"
        )

    top = Counter(r["norm"] for r in rows).most_common(10)
    print("\ntop repeated queries:")
    for norm, count in top:
        if count > 1:
            print(f"  {count}x  {norm}")

    after = await digest_db()
    mutated = {k: (before.get(k), after.get(k)) for k in before if before.get(k) != after.get(k)}

    payload = {
        "experiment": "search_query_reuse",
        "db_path": DB_PATH,
        "search_turns": total,
        "unique_queries": unique,
        "repeat_rate": round(repeat_rate, 3),
        "backend_mix": dict(backend_mix),
        "by_session_kind": breakdown,
        "windows": windows,
        "top_repeated": [{"query": q, "count": c} for q, c in top if c > 1],
        "db_digest_before": before,
        "db_digest_after": after,
        "db_mutated": mutated,
    }
    out = OUT_DIR / "result.json"
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out}")
    print(f"db mutated: {mutated or 'NO'}")


if __name__ == "__main__":
    asyncio.run(main())
