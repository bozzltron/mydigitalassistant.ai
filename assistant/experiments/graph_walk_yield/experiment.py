"""Does the graph walk recover memory the production path was leaving behind?

Read-only with respect to the brain. Varies only ``max_graph_frames`` and reports
what the walk contributes to the final context. See plan.md for the design,
the falsification conditions, and the threats to validity.

Never points at the live database: EXP_DB is a copy, and the live volume is not
mounted into the container this runs in. Table digests are captured before and
after so non-mutation is verifiable rather than asserted.

Usage:
    EXP_DB=/exp/assistant.db python experiment.py
Env:
    WALK_CAPS=0,7,20        conditions to run
    WALK_SALT=20260927      recorded provenance only; no sampling happens here
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.db.schema import _load_sqlite_vec, init_db
from assistant.backend.db.sqlcipher import aiosqlite_connect
from assistant.backend.memory.retrieval import Retriever, format_memory_context
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import OllamaClient, system_prompt_overhead

# The same 11 queries the frame budget experiment retained, reused so the
# numbers are comparable and so the gold facts are already screened for
# answerability. See plan.md threat 1.
QUERIES: list[tuple[str, str]] = [
    ("label_address", "What's the mailing address of the record label?"),
    ("cbs_hq", "Where is CBS News headquartered?"),
    ("perseverance", "What did the Perseverance rover accomplish, and when?"),
    ("penn_state", "What is Penn State's research group working on?"),
    ("mtw_members", "Who are the members of The Mountain & The Wolf?"),
    ("mtw_genre", "What genre is The Mountain & The Wolf and when does it come out?"),
    ("groover", "What is Groover and how big is it?"),
    ("encorespot", "When is the Encorespot application deadline?"),
    ("walking_cow", "Tell me about the Walking the Cow single."),
    ("jason_lee", "What is Jason Lee's skateboarding stance and when did he retire?"),
    ("festivaltopia", "What is Festivaltopia?"),
]

DB_PATH = os.environ.get("EXP_DB", "/exp/assistant.db")
OUT_DIR = Path(__file__).parent
CAPS = [int(c) for c in os.environ.get("WALK_CAPS", "0,7,20").split(",")]
USER_ID = int(os.environ.get("EXP_USER_ID", "1"))
# A fixed single owner: the walk filters neighbours by owner_user_id, so the
# number has to match a real user row or every neighbour is out of scope and the
# walk returns nothing for reasons that have nothing to do with the fix.
SALT = os.environ.get("WALK_SALT", "20260927")

# The memory section's real allowance, as Orchestrator._memory_char_budget computes
# it: the prompt cap minus the *measured* persona/task prefix. Using the bare
# max_system_prompt_chars would report a fit ~2k chars looser than production and
# hide truncation the user actually experiences. The functional task type with no
# plan and no self context is the smallest prefix, so this is the most generous
# budget production can hand out; a real turn's prefix is larger and the fit is
# correspondingly tighter. Reporting it keeps that direction of the bias visible.
MEMORY_CHAR_BUDGET = max(
    0,
    settings.max_system_prompt_chars - system_prompt_overhead("functional"),
)

DIGEST_TABLES = ("frames", "slots", "associations", "episodes", "slot_history", "conflicts")


async def digest_db() -> dict:
    """Row counts per table, as a non-mutation check."""
    async with aiosqlite_connect(DB_PATH) as db:
        await _load_sqlite_vec(db)
        out: dict[str, int] = {}
        for table in DIGEST_TABLES:
            try:
                out[table] = (await db.execute_fetchall(f"SELECT COUNT(*) FROM {table}"))[0][0]
            except Exception as exc:  # table absent in an older schema
                out[table] = f"n/a ({type(exc).__name__})"
        return out


def describe(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": round(ordered[0], 4),
        "p50": round(statistics.median(ordered), 4),
        "max": round(ordered[-1], 4),
        "mean": round(statistics.fmean(ordered), 4),
    }


async def main() -> None:
    if not Path(DB_PATH).exists():
        raise SystemExit(f"brain not found at {DB_PATH}")

    await init_db(DB_PATH)
    store = MemoryStore(DB_PATH)
    llm = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=settings.chat_model,
        keep_alive=settings.ollama_keep_alive,
    )

    before = await digest_db()
    print("db digest before:", json.dumps(before))

    # Must mirror main.py's construction exactly. Two things bite if you don't:
    #   - Retriever's own default is "nomic-embed-text" (768 dims) while the
    #     brain is embedded with settings.embedding_model (1024 dims); omitting
    #     the argument makes every search die on a dimension mismatch.
    #   - retriever.min_relevance is a *similarity* gate (default 0.3) and is NOT
    #     settings.retrieval_min_distance, which is a *distance* threshold (0.7)
    #     already passed to search_similar_frames as min_distance. Overriding
    #     min_relevance with it tightens similarity to 0.7 and silently empties
    #     the result set -- 5 of 11 queries returned zero frames before this line
    #     was removed. Production leaves min_relevance at its default.
    retriever = Retriever(
        store=store,
        llm_client=llm,
        embedding_model=settings.embedding_model,
    )
    print(
        f"user_id={USER_ID}  caps={CAPS}  "
        f"top_k_direct={retriever.top_k_direct}  min_relevance={retriever.min_relevance}"
    )

    results: dict[str, list[dict]] = {}
    for cap in CAPS:
        # Condition A is a control, not a production mode: it traverses the graph
        # and admits nothing, so the walk's contribution is isolated from its
        # cost. See plan.md falsification condition 3.
        retriever.max_graph_frames = cap
        rows: list[dict] = []
        for qid, text in QUERIES:
            t0 = time.perf_counter()
            ctx = await retriever.retrieve(text, user_id=USER_ID)
            latency_ms = (time.perf_counter() - t0) * 1000

            frames = ctx.retrieved_frames
            sources = [rf.source for rf in frames]
            walk = [rf for rf in frames if rf.source.startswith("graph_hop")]
            direct = [rf for rf in frames if rf.source == "direct_match"]
            # Unfitted: what the frame-aware fit is handed. Fitted: what the
            # model actually sees once whole frames are dropped to fit the
            # prompt. Counting headers is the honest way to count surviving
            # frames -- a truncated frame still renders its "### name" line and
            # so would be over-counted by anything else.
            unfitted = format_memory_context(ctx)
            fitted = format_memory_context(ctx, max_memory_chars=MEMORY_CHAR_BUDGET)
            # Count header *lines*. A bare substring count of "### " also matches
            # any slot value or URL that happens to contain it, which
            # over-counts (measured: 11 headers for a 10-frame context).
            headers = [ln for ln in fitted.splitlines() if ln.startswith("### ")]
            frames_fitted = len(headers)

            # Which frames actually reached the model, by source. This is the
            # number that matters, and it is NOT the pool share: the frame-aware
            # fit drops least-relevant-first, and walk frames carry decay (<=0.5)
            # so they sort below direct matches and are the first candidates
            # dropped when the char budget binds. Measuring the surviving share
            # is the only way to tell whether the walk delivers memory to the
            # model or merely to the candidate pool.
            walk_fitted = 0
            for header in headers:
                # Longest matching frame name wins, so "album" cannot claim a
                # header belonging to "album deluxe edition".
                best: str | None = None
                for rf in frames:
                    name = rf.frame.name
                    if header.startswith(f"### {name}") and (best is None or len(name) > len(best)):
                        best = name
                if best is not None:
                    for rf in frames:
                        if rf.frame.name == best and rf.source.startswith("graph_hop"):
                            walk_fitted += 1
                            break

            rows.append(
                {
                    "qid": qid,
                    "frames_returned": len(frames),
                    "walk_frames": len(walk),
                    "direct_frames": len(direct),
                    "walk_share": round(len(walk) / len(frames), 3) if frames else 0.0,
                    "walk_relevance": describe([rf.relevance for rf in walk]),
                    "direct_relevance": describe([rf.relevance for rf in direct]),
                    "memory_chars_unfitted": len(unfitted),
                    "memory_chars_fitted": len(fitted),
                    "frames_fitted": frames_fitted,
                    "walk_frames_fitted": walk_fitted,
                    "walk_share_fitted": (
                        round(walk_fitted / frames_fitted, 3) if frames_fitted else 0.0
                    ),
                    "frames_dropped_by_fit": len(frames) - frames_fitted,
                    "latency_ms": round(latency_ms, 1),
                    "sources": sources,
                }
            )
            print(
                f"  cap={cap:<3} {qid:<16} frames={len(frames):<3} "
                f"walk={len(walk):<3} ({rows[-1]['walk_share']:.0%} of pool)  "
                f"fitted={len(fitted):>6}ch/{frames_fitted:<3}fr "
                f"walk_surviving={walk_fitted:<3} ({rows[-1]['walk_share_fitted']:.0%})  "
                f"dropped {rows[-1]['frames_dropped_by_fit']}  {latency_ms:6.0f}ms"
            )
        results[str(cap)] = rows
        print()

    after = await digest_db()
    print("db digest after: ", json.dumps(after))
    mutated = {k: (before.get(k), after.get(k)) for k in before if before.get(k) != after.get(k)}

    all_walk = [r["walk_frames"] for rows in results.values() for r in rows]
    all_share = [r["walk_share"] for rows in results.values() for r in rows]
    cap_of = {c: [r["walk_share"] for r in results[str(c)]] for c in CAPS}
    cap_surv = {c: [r["walk_share_fitted"] for r in results[str(c)]] for c in CAPS}

    payload = {
        "experiment": "graph_walk_yield",
        "db_path": DB_PATH,
        "user_id": USER_ID,
        "salt": SALT,
        "caps": CAPS,
        "n_queries": len(QUERIES),
        "conditions": results,
        "summary": {
            "walk_frames_by_cap": cap_of,
            "walk_share_surviving_fit_by_cap": cap_surv,
            "mean_walk_frames_all_conditions": round(statistics.fmean(all_walk), 2)
            if all_walk
            else 0,
            "mean_walk_share_all_conditions": round(statistics.fmean(all_share), 3)
            if all_share
            else 0,
            "latency_by_cap_ms": {
                str(c): describe([r["latency_ms"] for r in results[str(c)]]) for c in CAPS
            },
            "memory_chars_unfitted_by_cap": {
                str(c): describe([r["memory_chars_unfitted"] for r in results[str(c)]])
                for c in CAPS
            },
            "memory_chars_fitted_by_cap": {
                str(c): describe([r["memory_chars_fitted"] for r in results[str(c)]])
                for c in CAPS
            },
            "frames_fitted_by_cap": {
                str(c): describe([r["frames_fitted"] for r in results[str(c)]]) for c in CAPS
            },
        },
        "memory_char_budget": MEMORY_CHAR_BUDGET,
        "db_digest_before": before,
        "db_digest_after": after,
        "db_mutated": mutated,
    }
    out = OUT_DIR / "result.json"
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out}")
    print(f"db mutated: {mutated or 'NO'}")

    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
